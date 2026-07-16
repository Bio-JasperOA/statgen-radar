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


def split_markdown_row(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def numeric_value(value: str) -> float | None:
    match = re.search(r"\d+(?:\.\d+)?", value or "")
    return float(match.group()) if match else None


def parse_inclusion_table(text: str, inclusion_date: str) -> list[dict]:
    lines = text.splitlines()
    records: list[dict] = []
    for index, line in enumerate(lines):
        if not line.lstrip().startswith("|"):
            continue
        headers = split_markdown_row(line)
        normalized = [header.lower() for header in headers]
        if "article" not in normalized or "total" not in normalized:
            continue
        if not any("journal" in header for header in normalized):
            continue
        if index + 1 >= len(lines) or not re.match(r"^\s*\|?\s*:?-+", lines[index + 1]):
            continue
        for row_line in lines[index + 2 :]:
            if not row_line.lstrip().startswith("|"):
                break
            values = split_markdown_row(row_line)
            if len(values) < len(headers):
                values.extend([""] * (len(headers) - len(values)))
            row = dict(zip(headers, values))
            record_type = row.get("Type", "")
            if record_type.lower() != "journal article":
                continue
            journal = row.get("Journal / platform", row.get("Journal", "")).strip()
            doi = row.get("DOI", "").strip()
            jif_text = row.get("2025 JIF", row.get("JIF", "")).strip()
            total_text = row.get("Total", "").strip()
            records.append(
                {
                    "inclusion_date": inclusion_date,
                    "article": row.get("Article", "").strip(),
                    "journal": journal,
                    "doi": doi,
                    "score": numeric_value(total_text),
                    "impact_factor": numeric_value(jif_text),
                    "impact_factor_label": jif_text,
                    "published": row.get("Published", "").strip(),
                    "brief_url": f"/statgen-radar/article.html?date={inclusion_date}",
                }
            )
        break
    return records


def build_journal_index(reports_dir: Path) -> list[dict]:
    by_key: dict[str, dict] = {}
    for report_path in sorted(reports_dir.glob("*.md")):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", report_path.stem):
            continue
        text = report_path.read_text(encoding="utf-8")
        for record in parse_inclusion_table(text, report_path.stem):
            key = record["doi"].lower() if record["doi"] else (
                record["article"].lower() + "|" + record["journal"].lower()
            )
            previous = by_key.get(key)
            if previous is None or record["inclusion_date"] < previous["inclusion_date"]:
                by_key[key] = record
    rows = list(by_key.values())
    rows.sort(
        key=lambda row: (
            -(row["impact_factor"] if row["impact_factor"] is not None else -1),
            -(row["score"] if row["score"] is not None else -1),
            row["journal"].lower(),
            row["article"].lower(),
        )
    )
    return rows


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
        "jif_edition": parse_value(text, "JIF edition", "2025"),
        "url": f"/statgen-radar/article.html?date={date}",
    }

    archive = [row for row in archive if row.get("date") != date]
    archive.append(item)
    archive.sort(key=lambda row: row.get("date", ""), reverse=True)
    archive_path.write_text(
        json.dumps(archive, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    journal_index = build_journal_index(reports_dir)
    journal_index_path = website / "data" / "statgen-radar-journals.json"
    journal_index_path.write_text(
        json.dumps(journal_index, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"Published {report.name}; archive entries={len(archive)}; "
        f"indexed journal articles={len(journal_index)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
