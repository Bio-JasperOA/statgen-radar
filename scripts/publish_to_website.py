#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path


def parse_value(text: str, label: str, default: int | str = 0):
    match = re.search(rf"^{re.escape(label)}:\s*(.+?)\s*$", text, re.MULTILINE)
    if not match:
        return default
    value = match.group(1).strip()
    if isinstance(default, int):
        number = re.search(r"\d+", value)
        return int(number.group()) if number else default
    return value


def executive_summary(text: str) -> str:
    match = re.search(
        r"^##\s+(?:\d+\.\s+)?Executive summary\s*$\n+(.+?)(?:\n\n|\n##\s)",
        text,
        re.MULTILINE | re.DOTALL,
    )
    if not match:
        return "Daily statistical-genetics literature brief."
    paragraph = re.sub(r"\s+", " ", match.group(1)).strip()
    return paragraph[:320] + ("…" if len(paragraph) > 320 else "")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    parser.add_argument("website", type=Path)
    args = parser.parse_args()

    report = args.report.resolve()
    website = args.website.resolve()
    if not report.is_file():
        raise FileNotFoundError(report)

    date = report.stem
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        raise ValueError(f"Unexpected report filename: {report.name}")

    text = report.read_text(encoding="utf-8")
    reports_dir = website / "statgen-radar" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(report, reports_dir / report.name)

    archive_path = website / "data" / "statgen-radar.json"
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    if archive_path.exists():
        archive = json.loads(archive_path.read_text(encoding="utf-8"))
    else:
        archive = []

    included = parse_value(text, "Included records after quality control", 0)
    if not included:
        included = parse_value(text, "Relevant records", 0)

    item = {
        "date": date,
        "title": "StatGen Radar — Daily Brief",
        "summary": executive_summary(text),
        "records": included,
        "journal_articles": parse_value(text, "Journal articles", 0),
        "preprints": parse_value(text, "Preprints", 0),
        "jif_edition": parse_value(text, "JIF edition", "Unknown"),
        "url": f"/statgen-radar/article.html?date={date}",
    }

    archive = [row for row in archive if row.get("date") != date]
    archive.append(item)
    archive.sort(key=lambda row: row.get("date", ""), reverse=True)
    archive_path.write_text(
        json.dumps(archive, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Published {report.name}; archive entries={len(archive)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
