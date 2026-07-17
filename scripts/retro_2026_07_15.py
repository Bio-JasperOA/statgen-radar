#!/usr/bin/env python3
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import statgen_radar as radar
import statgen_radar_ranked as ranked
import run_ranked_with_tsv as ranked_tsv

TARGET = datetime(2026, 7, 15, 23, 59, 59, tzinfo=timezone.utc)


class FixedDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return TARGET if tz is not None else TARGET.replace(tzinfo=None)


radar.datetime = FixedDateTime
ranked.datetime = FixedDateTime
ranked.load_external_metric_rows = ranked_tsv.load_tsv_rows


def main() -> int:
    days = 0
    min_score = 3
    keywords = radar.load_keywords()
    metric_config, metric_lookup = ranked.load_metrics()

    collectors = {
        "RSS": radar.collect_rss(days),
        "Priority journals": radar.collect_priority_journals(days),
        "Europe PMC": radar.collect_europe_pmc(days),
        "Crossref": radar.collect_crossref(days),
        "arXiv": radar.collect_arxiv(days),
        "bioRxiv": radar.collect_rxiv("biorxiv", days),
        "medRxiv": radar.collect_rxiv("medrxiv", days),
    }
    for name, records in collectors.items():
        print(f"SOURCE {name}: collected={len(records)}")

    collected = [article for records in collectors.values() for article in records]
    scored = [radar.score_article(article, keywords) for article in collected if article.title]
    relevant = [article for article in scored if article.score >= min_score]
    unique = radar.deduplicate(relevant)
    ranked_articles = [ranked.add_publication_score(article, metric_config, metric_lookup) for article in unique]

    out_dir = ROOT / "reports" / "retrospective"
    out_dir.mkdir(parents=True, exist_ok=True)
    original_report = ranked.report(ranked_articles, "daily", days)
    target_report = out_dir / "2026-07-15.md"
    target_report.write_text(original_report.read_text(encoding="utf-8"), encoding="utf-8")
    original_report.unlink(missing_ok=True)

    print(f"Collected={len(collected)} relevant={len(ranked_articles)} report={target_report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
