#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def escape_cell(value: str) -> str:
    return (value or "").replace("|", "\\|").replace("\n", " ").strip()


def field(block: str, label: str, default: str = "—") -> str:
    match = re.search(rf"^- \*\*{re.escape(label)}:\*\*\s*(.+)$", block, flags=re.MULTILINE)
    return match.group(1).strip() if match else default


def parse_records(text: str) -> list[dict[str, str]]:
    pattern = re.compile(r"^###\s+\d+\.\s+(.+?)\n(.*?)(?=^###\s+\d+\.|\Z)", re.MULTILINE | re.DOTALL)
    records: list[dict[str, str]] = []
    for title, block in pattern.findall(text):
        journal = field(block, "Journal / platform", field(block, "Journal", field(block, "Platform")))
        records.append({
            "title": title.strip(),
            "record_type": field(block, "Record type"),
            "journal": journal,
            "jif": field(block, "Impact factor", field(block, "2025 JIF")),
            "published": field(block, "Published"),
            "relevance": field(block, "Relevance score"),
            "publication": field(block, "Publication score"),
            "total": field(block, "Total score"),
            "doi": field(block, "DOI"),
        })
    return records


def build_table(records: list[dict[str, str]]) -> str:
    lines = [
        "## Full inclusion table",
        "",
        f"All {len(records)} records retained after relevance screening are listed below. Detailed interpretation may focus on a smaller priority subset, but no included record is omitted from this table.",
        "",
        "| No. | Article | Type | Journal / platform | JIF | Published | Relevance | Publication | Total | DOI |",
        "|---:|---|---|---|---:|---|---:|---:|---:|---|",
    ]
    for index, record in enumerate(records, 1):
        lines.append(
            "| {index} | {title} | {record_type} | {journal} | {jif} | {published} | {relevance} | {publication} | {total} | {doi} |".format(
                index=index,
                **{key: escape_cell(value) for key, value in record.items()},
            )
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["daily", "weekly"], default="daily")
    parser.add_argument("--date", default=datetime.now(timezone.utc).date().isoformat())
    args = parser.parse_args()

    report_dir = ROOT / "reports" / ("weekly" if args.mode == "weekly" else "daily")
    path = report_dir / f"{args.date}.md"
    text = path.read_text(encoding="utf-8")
    records = parse_records(text)
    if not records:
        raise ValueError(f"No article records could be parsed from {path}")

    text = re.sub(r"\n## Full inclusion table\n.*?(?=\n## |\Z)", "", text, flags=re.DOTALL)
    table = build_table(records)
    marker = "\n## Priority reading\n"
    if marker in text:
        text = text.replace(marker, f"\n{table}\n{marker}", 1)
    else:
        text = f"{text.rstrip()}\n\n{table}\n"
    path.write_text(text, encoding="utf-8")
    print(f"Added full inclusion table with {len(records)} records to {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
