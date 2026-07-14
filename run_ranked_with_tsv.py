#!/usr/bin/env python3
from __future__ import annotations

import csv
from pathlib import Path

import statgen_radar_ranked as ranked

ROOT = Path(__file__).resolve().parent
TSV_PATH = ROOT / "config" / "journal_metrics_2025.tsv"
REQUIRED_COLUMNS = {"journal", "abbreviation", "impact_factor"}


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


ranked.load_external_metric_rows = load_tsv_rows

if __name__ == "__main__":
    raise SystemExit(ranked.main())
