#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import csv
import gzip
import io
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import yaml

import statgen_radar as radar

ROOT = Path(__file__).resolve().parent
METRICS_PATH = ROOT / "config" / "journal_metrics.yml"
DEFAULT_MIN_RELEVANCE_SCORE = 10
DEFAULT_MIN_AI_SCORE = 5
DEFAULT_MIN_LIFE_SCIENCE_SCORE = 4
DEFAULT_DAILY_LIMIT = 10
DEFAULT_WEEKLY_LIMIT = 30


def normalize_journal(value: str) -> str:
    return radar.normalize(value)


def journal_name(article: radar.Article) -> str:
    if article.record_type == "Preprint":
        return article.source
    if " / " in article.source:
        return article.source.split(" / ", 1)[1].strip()
    return article.source.strip()


def load_external_metric_rows(config: dict) -> list[dict]:
    pattern = config.get("data_parts_glob")
    if not pattern:
        return []
    parts = sorted((ROOT / "config").glob(pattern))
    if not parts:
        raise FileNotFoundError(f"No journal metric parts matched config/{pattern}")
    encoded = "".join(part.read_text(encoding="ascii").strip() for part in parts)
    raw = gzip.decompress(base64.b64decode(encoded)).decode("utf-8")
    return list(csv.DictReader(io.StringIO(raw), delimiter="\t"))


def load_metrics() -> tuple[dict, dict[str, dict]]:
    with METRICS_PATH.open(encoding="utf-8") as handle:
        config = yaml.safe_load(handle) or {}
    lookup: dict[str, dict] = {}

    for row in load_external_metric_rows(config):
        canonical = (row.get("journal") or "").strip()
        abbreviation = (row.get("abbreviation") or "").strip()
        raw_jif = (row.get("impact_factor") or "").strip()
        if not canonical or not raw_jif:
            continue
        try:
            impact_factor = float(raw_jif)
        except ValueError:
            continue
        entry = {
            "journal": canonical,
            "impact_factor": impact_factor,
            "source_note": config.get("data_source_note", ""),
        }
        for name in (canonical, abbreviation):
            if name:
                lookup[normalize_journal(name)] = entry

    # Manual values and aliases take precedence over the imported table.
    for canonical, values in (config.get("journals") or {}).items():
        entry = {
            "journal": canonical,
            "impact_factor": float(values["impact_factor"]),
            "source_note": values.get("source_note", ""),
        }
        for name in [canonical, *(values.get("aliases") or [])]:
            lookup[normalize_journal(name)] = entry
    return config, lookup


def tier_score(impact_factor: float, tiers: list[dict]) -> int:
    ordered = sorted(tiers, key=lambda row: float(row["min_if"]), reverse=True)
    for row in ordered:
        if impact_factor >= float(row["min_if"]):
            return int(row["score"])
    return 0


def add_publication_score(
    article: radar.Article, config: dict, lookup: dict[str, dict]
) -> radar.Article:
    article.relevance_score = article.score
    journal = journal_name(article)
    article.metric_name = config.get("metric_name", "Journal Impact Factor")
    article.metric_year = config.get("metric_year", "Unknown")
    article.impact_factor = None
    article.metric_source = ""

    if article.record_type == "Preprint":
        article.publication_score = int(config.get("preprint_publication_score", 4))
        article.publication_tier = "Preprint (uniform score)"
    else:
        metric = lookup.get(normalize_journal(journal))
        if metric:
            article.impact_factor = float(metric["impact_factor"])
            article.metric_source = metric.get("source_note", "")
            article.publication_score = tier_score(
                article.impact_factor, config.get("tiers") or []
            )
            article.publication_tier = f"JIF tier: {article.publication_score:+d}"
        else:
            article.publication_score = int(config.get("unknown_journal_score", 0))
            article.publication_tier = "JIF not configured"

    article.total_score = article.relevance_score + article.publication_score
    return article


def metric_label(article: radar.Article) -> str:
    if article.record_type == "Preprint":
        return "Not applicable (preprint)"
    if article.impact_factor is None:
        return f"Not configured ({article.metric_year})"
    return f"{article.impact_factor:.1f} ({article.metric_year})"


def ranking_key(article: radar.Article) -> tuple:
    impact_factor = article.impact_factor if article.impact_factor is not None else -1
    return (
        -article.total_score,
        -article.priority_score,
        -(article.ai_score + article.life_science_score),
        -impact_factor,
        article.published,
        article.title,
    )


def passes_profile(
    article: radar.Article,
    *,
    min_relevance_score: int,
    min_ai_score: int,
    min_life_science_score: int,
    preprint_min_relevance_score: int,
) -> bool:
    required_relevance = (
        preprint_min_relevance_score
        if article.record_type == "Preprint"
        else min_relevance_score
    )
    return (
        radar.is_allowed_source(article)
        and not article.excluded_primary_purpose
        and not article.excluded_content_type
        and not article.excluded_domain_noise
        and article.ai_score >= min_ai_score
        and article.life_science_score >= min_life_science_score
        and article.relevance_score >= required_relevance
    )


def executive_summary(articles: list[radar.Article]) -> str:
    journal_count = sum(article.record_type == "Journal article" for article in articles)
    preprint_count = sum(article.record_type == "Preprint" for article in articles)
    if not articles:
        return (
            "No top-journal article or arXiv/bioRxiv preprint passed both the AI "
            "and life-science gates in this window."
        )
    priority_count = sum(article.priority_score > 0 for article in articles)
    sentence = (
        f"This brief retained {journal_count} top-journal article(s) and "
        f"{preprint_count} arXiv/bioRxiv preprint(s) after independent AI and "
        "life-science screening."
    )
    if priority_count:
        sentence += (
            f" {priority_count} record(s) directly matched Virtual Embryo, "
            "developmental-dynamics, single-cell or spatial-omics priorities."
        )
    return sentence


def report(
    articles: list[radar.Article],
    mode: str,
    days: int,
    *,
    collected_count: int | None = None,
    candidate_count: int | None = None,
    eligible_count: int | None = None,
    min_relevance_score: int = DEFAULT_MIN_RELEVANCE_SCORE,
    min_ai_score: int = DEFAULT_MIN_AI_SCORE,
    min_life_science_score: int = DEFAULT_MIN_LIFE_SCIENCE_SCORE,
    preprint_min_relevance_score: int = 12,
    record_limit: int | None = None,
    metric_year: int | str = "2025",
) -> Path:
    now = datetime.now(timezone.utc)
    out_dir = ROOT / "reports" / ("weekly" if mode == "weekly" else "daily")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{now.date().isoformat()}.md"
    radar.preserve_legacy_report(path, mode)

    ranked = sorted(articles, key=ranking_key)
    collected_count = len(ranked) if collected_count is None else collected_count
    candidate_count = len(ranked) if candidate_count is None else candidate_count
    eligible_count = len(ranked) if eligible_count is None else eligible_count
    type_counts = Counter(article.record_type for article in ranked)
    source_counts = Counter(article.source.split(" / ", 1)[0] for article in ranked)
    configured_jif = sum(
        article.record_type == "Journal article" and article.impact_factor is not None
        for article in ranked
    )
    filtered_out = max(candidate_count - eligible_count, 0)
    omitted_by_limit = max(eligible_count - len(ranked), 0)

    lines = [
        f"# {radar.DISPLAY_NAME} — {mode.title()} Brief",
        "",
        f"Profile: {radar.PROFILE}",
        f"Generated: {now.isoformat(timespec='minutes')}",
        f"Window: last {days} day(s)",
        f"Collected records: {collected_count}",
        f"Scored unique candidates: {candidate_count}",
        f"Eligible before limit: {eligible_count}",
        f"Passed threshold: {len(ranked)}",
        f"Filtered out: {filtered_out}",
        f"Omitted by report limit: {omitted_by_limit}",
        f"Journal articles: {type_counts.get('Journal article', 0)}",
        f"Top-journal articles: {type_counts.get('Journal article', 0)}",
        f"Preprints: {type_counts.get('Preprint', 0)}",
        f"Journal articles with configured JIF: {configured_jif}",
        f"JIF edition: {metric_year}",
        f"Report limit: {record_limit if record_limit is not None else len(ranked)}",
        "",
        "## Executive summary",
        "",
        executive_summary(ranked),
        "",
        "## Scoring model",
        "",
        "Eligibility is a hard intersection: AI fit, life-science fit, and an allowed source must all pass.",
        f"AI fit >= {min_ai_score}; life-science fit >= {min_life_science_score}.",
        f"Topic relevance >= {min_relevance_score} for exact-whitelist journals and >= {preprint_min_relevance_score} for arXiv/bioRxiv.",
        "Work led by GWAS, PRS, Mendelian randomization or related statistical-genetics aims is excluded.",
        "Total score = topic relevance + publication score. JIF affects ranking only; it never grants eligibility.",
        "",
        "## Source coverage",
        "",
    ]
    if source_counts:
        lines.extend(f"- **{source}:** {count}" for source, count in source_counts.most_common())
    else:
        lines.append("No source returned a record above both topic gates.")
    lines += ["", "## Priority reading", ""]

    if not ranked:
        lines.append("No records met the AI for Life Science profile in this run.")

    for index, article in enumerate(ranked, 1):
        excerpt = article.abstract[:700] + ("…" if len(article.abstract) > 700 else "")
        lines += [
            f"### {index}. {article.title}",
            "",
            f"- **Record type:** {article.record_type}",
            f"- **Journal / platform:** {article.journal}",
            *([f"- **Category:** {article.categories}"] if article.categories else []),
            f"- **Impact factor:** {metric_label(article)}",
            f"- **Source:** {article.source}",
            f"- **Published:** {article.published or 'Unknown'}",
            f"- **Total score:** {article.total_score}",
            f"- **Relevance score:** {article.relevance_score}",
            f"- **AI fit:** {article.ai_score}",
            f"- **Life-science fit:** {article.life_science_score}",
            f"- **Priority fit:** {article.priority_score}",
            f"- **Publication score:** {article.publication_score} ({article.publication_tier})",
            f"- **Matched terms:** {article.matched_terms or 'None'}",
            f"- **Authors:** {article.authors or 'Not provided'}",
            f"- **DOI:** {article.doi or 'Not provided'}",
            f"- **Link:** {article.url or 'Not provided'}",
            "",
            excerpt or "Abstract unavailable.",
            "",
        ]

    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=1)
    parser.add_argument("--mode", choices=["daily", "weekly"], default="daily")
    parser.add_argument(
        "--min-score",
        type=int,
        default=None,
        help="Override the configured minimum topic relevance score",
    )
    parser.add_argument("--min-ai-score", type=int, default=None)
    parser.add_argument("--min-life-science-score", type=int, default=None)
    parser.add_argument("--max-records", type=int, default=None)
    # Retained only so existing invocations do not fail. Source eligibility is
    # now controlled by the exact journal whitelist or arXiv/bioRxiv.
    parser.add_argument("--min-publication-score", type=int, default=None, help=argparse.SUPPRESS)
    args = parser.parse_args()

    keyword_config = radar.load_keywords()
    thresholds = keyword_config.get("thresholds") or {}
    min_relevance_score = (
        args.min_score if args.min_score is not None else thresholds.get("topic_total", 10)
    )
    min_ai_score = (
        args.min_ai_score if args.min_ai_score is not None else thresholds.get("ai", 5)
    )
    min_life_science_score = (
        args.min_life_science_score
        if args.min_life_science_score is not None
        else thresholds.get("life_science", 4)
    )
    preprint_min_relevance_score = max(
        min_relevance_score, thresholds.get("preprint_topic_total", 12)
    )
    configured_limit = thresholds.get(
        "daily_limit" if args.mode == "daily" else "weekly_limit",
        DEFAULT_DAILY_LIMIT if args.mode == "daily" else DEFAULT_WEEKLY_LIMIT,
    )
    requested_limit = args.max_records if args.max_records is not None else configured_limit
    record_limit = max(0, min(requested_limit, DEFAULT_DAILY_LIMIT)) if args.mode == "daily" else max(0, requested_limit)

    metric_config, metric_lookup = load_metrics()
    print(
        f"JIF lookup entries={len(metric_lookup)} "
        f"metric_year={metric_config.get('metric_year', 'Unknown')}"
    )
    collectors = {
        "RSS": radar.collect_rss(args.days),
        "Top journals": radar.collect_priority_journals(args.days),
        "Europe PMC": radar.collect_europe_pmc(args.days),
        "Crossref": radar.collect_crossref(args.days),
        "arXiv": radar.collect_arxiv(args.days),
        "bioRxiv": radar.collect_rxiv("biorxiv", args.days),
    }
    for name, records in collectors.items():
        print(f"SOURCE {name}: collected={len(records)}")

    collected = [article for records in collectors.values() for article in records]
    scored = [
        radar.score_article(article, keyword_config)
        for article in collected
        if article.title
    ]
    unique = radar.deduplicate(scored)
    candidates = [
        add_publication_score(article, metric_config, metric_lookup)
        for article in unique
    ]
    eligible = [
        article
        for article in candidates
        if passes_profile(
            article,
            min_relevance_score=min_relevance_score,
            min_ai_score=min_ai_score,
            min_life_science_score=min_life_science_score,
            preprint_min_relevance_score=preprint_min_relevance_score,
        )
    ]
    retained = sorted(eligible, key=ranking_key)[:record_limit]

    connection = radar.init_db()
    inserted = radar.save(connection, retained)
    path = report(
        retained,
        args.mode,
        args.days,
        collected_count=len(collected),
        candidate_count=len(candidates),
        eligible_count=len(eligible),
        min_relevance_score=min_relevance_score,
        min_ai_score=min_ai_score,
        min_life_science_score=min_life_science_score,
        preprint_min_relevance_score=preprint_min_relevance_score,
        record_limit=record_limit,
        metric_year=metric_config.get("metric_year", "Unknown"),
    )
    print(
        f"Collected={len(collected)} candidates={len(candidates)} "
        f"eligible={len(eligible)} retained={len(retained)} "
        f"journal={sum(article.record_type == 'Journal article' for article in retained)} "
        f"preprint={sum(article.record_type == 'Preprint' for article in retained)} "
        f"inserted={inserted} report={path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
