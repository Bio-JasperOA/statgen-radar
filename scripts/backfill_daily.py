#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

import add_inclusion_table
import run_ranked_with_tsv as ranked_tsv
import statgen_radar as radar
import statgen_radar_ranked as ranked

ROOT = Path(__file__).resolve().parents[1]


def run_for_date(date_text: str, min_score: int) -> Path:
    target_date = datetime.strptime(date_text, "%Y-%m-%d").date()
    target_dt = datetime.combine(target_date, datetime.min.time(), tzinfo=timezone.utc)

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return target_dt if tz is not None else target_dt.replace(tzinfo=None)

    def within_target_day(value: str, days: int) -> bool:
        parsed = radar.parse_date(value)
        return not parsed or parsed.date() == target_date

    radar.datetime = FixedDateTime
    ranked.datetime = FixedDateTime
    radar.within_days = within_target_day
    ranked.load_external_metric_rows = ranked_tsv.load_tsv_rows

    keywords = radar.load_keywords()
    metric_config, metric_lookup = ranked.load_metrics()
    collectors = {
        "RSS": radar.collect_rss(0),
        "Priority journals": radar.collect_priority_journals(0),
        "Europe PMC": radar.collect_europe_pmc(0),
        "Crossref": radar.collect_crossref(0),
        "arXiv": radar.collect_arxiv(0),
        "bioRxiv": radar.collect_rxiv("biorxiv", 0),
        "medRxiv": radar.collect_rxiv("medrxiv", 0),
    }
    for name, records in collectors.items():
        print(f"BACKFILL {date_text} SOURCE {name}: collected={len(records)}")

    collected = [article for records in collectors.values() for article in records]
    scored = [radar.score_article(article, keywords) for article in collected if article.title]
    relevant = [article for article in scored if article.score >= min_score]
    unique = radar.deduplicate(relevant)
    ranked_articles = [
        ranked.add_publication_score(article, metric_config, metric_lookup)
        for article in unique
    ]

    connection = radar.init_db()
    radar.save(connection, ranked_articles)
    path = ranked.report(ranked_articles, "daily", 0)

    text = path.read_text(encoding="utf-8")
    records = add_inclusion_table.parse_records(text)
    if records:
        table = add_inclusion_table.build_table(records)
        marker = "\n## Priority reading\n"
        text = text.replace(marker, f"\n{table}\n{marker}", 1)
        path.write_text(text, encoding="utf-8")

    print(
        f"BACKFILL {date_text}: collected={len(collected)} "
        f"relevant={len(ranked_articles)} report={path}"
    )
    return path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("dates", nargs="+", help="Dates in YYYY-MM-DD format")
    parser.add_argument("--min-score", type=int, default=3)
    args = parser.parse_args()

    completed = []
    for date_text in args.dates:
        completed.append(run_for_date(date_text, args.min_score))

    marker = ROOT / "config" / "backfill_dates.txt"
    marker.unlink(missing_ok=True)
    done = ROOT / ".backfill_dates_done"
    done.write_text("\n".join(path.stem for path in completed) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
