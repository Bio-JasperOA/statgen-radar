#!/usr/bin/env python3
from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path

import statgen_radar_ranked as ranked

ROOT = Path(__file__).resolve().parent
TSV_PATH = ROOT / "config" / "journal_metrics_2025.tsv"
REQUIRED_COLUMNS = {"journal", "abbreviation", "impact_factor"}

ADDITIONAL_SEARCH_TERMS = [
    '"disease trajectory"',
    '"multimorbidity trajectory"',
    '"multi-state model"',
    '"gene-environment interaction"',
    '"interaction GWAS"',
    '"cell-state eQTL"',
    '"response eQTL"',
    '"somatic evolution"',
    '"clonal hematopoiesis"',
    '"cardio-oncology"',
    '"shared genetic architecture"',
    '"longitudinal multi-omics"',
    '"disease foundation model"',
]

ADDITIONAL_CROSSREF_QUERIES = [
    "disease trajectory multimorbidity multi-state transition-specific genetics",
    "gene-environment interaction interaction GWAS context-specific genetic effect",
    "cell-state eQTL response eQTL condition-specific eQTL",
    "somatic evolution clonal hematopoiesis germline somatic interaction",
    "cardio-oncology cancer cardiovascular shared genetic architecture",
    "longitudinal multi-omics disease foundation model digital twin",
]


def load_tsv_rows(config: dict) -> list[dict]:
    if not TSV_PATH.exists():
        raise FileNotFoundError(f"Missing {TSV_PATH.relative_to(ROOT)}")
    if TSV_PATH.stat().st_size == 0:
        raise ValueError(f"{TSV_PATH.relative_to(ROOT)} is empty")

    with TSV_PATH.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        fields = set(reader.fieldnames or [])
        missing = REQUIRED_COLUMNS - fields
        if missing:
            raise ValueError(
                f"TSV missing required columns: {', '.join(sorted(missing))}; "
                f"found: {', '.join(reader.fieldnames or [])}"
            )
        rows = list(reader)

    if len(rows) < 1000:
        raise ValueError(f"TSV contains only {len(rows)} data rows; expected a full JIF table")
    print(f"Loaded JIF TSV rows={len(rows)} path={TSV_PATH.relative_to(ROOT)}")
    return rows


_original_collect_crossref = ranked.radar.collect_crossref


def collect_crossref_expanded(days: int) -> list[ranked.radar.Article]:
    """Run the core Crossref search plus project-specific trajectory searches."""
    rows = _original_collect_crossref(days)
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=days)
    select = (
        "DOI,title,abstract,author,published-online,published-print,"
        "published,issued,created,URL,container-title"
    )

    for query_text in ADDITIONAL_CROSSREF_QUERIES:
        rows.extend(
            ranked.radar.crossref_request(
                {
                    "query.bibliographic": query_text,
                    "filter": (
                        f"from-pub-date:{start},until-pub-date:{end},"
                        "type:journal-article"
                    ),
                    "sort": "published",
                    "order": "desc",
                    "rows": 300,
                    "select": select,
                },
                f"project topic {query_text}",
            )
        )
    return rows


for search_term in ADDITIONAL_SEARCH_TERMS:
    if search_term not in ranked.radar.SEARCH_TERMS:
        ranked.radar.SEARCH_TERMS.append(search_term)

ranked.radar.collect_crossref = collect_crossref_expanded
ranked.load_external_metric_rows = load_tsv_rows

if __name__ == "__main__":
    raise SystemExit(ranked.main())
