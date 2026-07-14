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
    with open(METRICS_PATH, encoding="utf-8") as handle:
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

    # Optional manual overrides take precedence over the imported table.
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


def add_publication_score(article: radar.Article, config: dict, lookup: dict[str, dict]) -> radar.Article:
    article.relevance_score = article.score
    article.journal = journal_name(article)
    article.metric_name = config.get("metric_name", "Journal Impact Factor")
    article.metric_year = config.get("metric_year", "Unknown")
    article.impact_factor = None
    article.metric_source = ""

    if article.record_type == "Preprint":
        article.publication_score = int(config.get("preprint_publication_score", 3))
        article.publication_tier = "Preprint (uniform score)"
    else:
        metric = lookup.get(normalize_journal(article.journal))
        if metric:
            article.impact_factor = float(metric["impact_factor"])
            article.metric_source = metric.get("source_note", "")
            article.publication_score = tier_score(article.impact_factor, config.get("tiers") or [])
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


def report(articles: list[radar.Article], mode: str, days: int) -> Path:
    now = datetime.now(timezone.utc)
    out_dir = ROOT / "reports" / ("weekly" if mode == "weekly" else "daily")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{now.date().isoformat()}.md"
    ranked = sorted(
        articles,
        key=lambda a: (-a.total_score, -a.relevance_score, -(a.impact_factor or -1), a.published, a.title),
    )
    type_counts = Counter(a.record_type for a in ranked)
    source_counts = Counter(a.source.split(" / ", 1)[0] for a in ranked)
    configured_jif = sum(a.record_type == "Journal article" and a.impact_factor is not None for a in ranked)

    lines = [
        f"# StatGen Radar — {mode.title()} Brief",
        "",
        f"Generated: {now.isoformat(timespec='minutes')}",
        f"Window: last {days} day(s)",
        f"Relevant records: {len(ranked)}",
        f"Journal articles: {type_counts.get('Journal article', 0)}",
        f"Preprints: {type_counts.get('Preprint', 0)}",
        f"Journal articles with configured JIF: {configured_jif}",
        "",
        "## Scoring model",
        "",
        "Total score = relevance score + publication score.",
        "Relevance screening is performed before publication-tier scoring.",
        "Preprints receive a uniform publication score; journal articles receive a tiered score from configured JIF values.",
        "",
        "## Source coverage",
        "",
    ]
    if source_counts:
        lines.extend(f"- **{source}:** {count}" for source, count in source_counts.most_common())
    else:
        lines.append("No source returned a record above the relevance threshold.")
    lines += ["", "## Priority reading", ""]

    if not ranked:
        lines.append("No records met the relevance threshold in this run.")

    for index, article in enumerate(ranked, 1):
        excerpt = article.abstract[:700] + ("…" if len(article.abstract) > 700 else "")
        lines += [
            f"### {index}. {article.title}",
            "",
            f"- **Record type:** {article.record_type}",
            f"- **Journal / platform:** {article.journal}",
            f"- **Impact factor:** {metric_label(article)}",
            f"- **Source:** {article.source}",
            f"- **Published:** {article.published or 'Unknown'}",
            f"- **Total score:** {article.total_score}",
            f"- **Relevance score:** {article.relevance_score}",
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
    parser.add_argument("--min-score", type=int, default=3, help="Minimum relevance score before publication bonus")
    args = parser.parse_args()

    keywords = radar.load_keywords()
    metric_config, metric_lookup = load_metrics()
    print(f"JIF lookup entries={len(metric_lookup)} metric_year={metric_config.get('metric_year', 'Unknown')}")
    collectors = {
        "RSS": radar.collect_rss(args.days),
        "Europe PMC": radar.collect_europe_pmc(args.days),
        "Crossref": radar.collect_crossref(args.days),
        "arXiv": radar.collect_arxiv(args.days),
        "bioRxiv": radar.collect_rxiv("biorxiv", args.days),
        "medRxiv": radar.collect_rxiv("medrxiv", args.days),
    }
    for name, records in collectors.items():
        print(f"SOURCE {name}: collected={len(records)}")

    collected = [article for records in collectors.values() for article in records]
    scored = [radar.score_article(article, keywords) for article in collected if article.title]
    relevant = [article for article in scored if article.score >= args.min_score]
    unique = radar.deduplicate(relevant)
    ranked = [add_publication_score(article, metric_config, metric_lookup) for article in unique]

    connection = radar.init_db()
    inserted = radar.save(connection, ranked)
    path = report(ranked, args.mode, args.days)
    print(
        f"Collected={len(collected)} relevant={len(ranked)} "
        f"journal={sum(a.record_type == 'Journal article' for a in ranked)} "
        f"preprint={sum(a.record_type == 'Preprint' for a in ranked)} "
        f"inserted={inserted} report={path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
