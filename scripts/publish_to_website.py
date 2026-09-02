#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path

PROFILE = "ai-for-life-science"
DISPLAY_NAME = "AI for Life Science Radar"
DOI_SENTINELS = {
    "",
    "not provided",
    "unavailable",
    "na",
    "n/a",
    "—",
    "-",
    "none",
    "unknown",
}


def parse_value(text: str, label: str, default: int | str = 0):
    match = re.search(rf"^{re.escape(label)}:\s*(.+?)\s*$", text, re.MULTILINE)
    if not match:
        return default
    value = match.group(1).strip()
    if isinstance(default, int):
        number = re.search(r"\d+", value)
        return int(number.group()) if number else default
    return value


def is_current_profile(text: str) -> bool:
    return parse_value(text, "Profile", "") == PROFILE


def executive_summary(text: str) -> str:
    match = re.search(
        r"^##\s+(?:\d+\.\s+)?Executive summary\s*$\n+(.+?)(?:\n\n|\n##\s)",
        text,
        re.MULTILINE | re.DOTALL,
    )
    if not match:
        return "Daily intelligence on AI methods for the life sciences."
    paragraph = re.sub(r"\s+", " ", match.group(1)).strip()
    return paragraph[:320] + ("…" if len(paragraph) > 320 else "")


def split_markdown_row(line: str) -> list[str]:
    """Split on Markdown table delimiters while preserving escaped pipes."""
    text = line.strip()
    cells: list[str] = []
    current: list[str] = []
    index = 0
    while index < len(text):
        character = text[index]
        if character == "\\" and index + 1 < len(text):
            following = text[index + 1]
            if following in {"|", "\\"}:
                current.append(following)
                index += 2
                continue
        if character == "|":
            cells.append("".join(current).strip())
            current = []
        else:
            current.append(character)
        index += 1
    cells.append("".join(current).strip())
    if cells and not cells[0]:
        cells.pop(0)
    if cells and not cells[-1]:
        cells.pop()
    return cells


def normalize_doi(value: str) -> str:
    text = str(value or "").strip()
    if text.casefold() in DOI_SENTINELS:
        return ""
    text = re.sub(
        r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", text, flags=re.I
    ).strip().rstrip(".")
    return "" if text.casefold() in DOI_SENTINELS else text


def numeric_value(value: str) -> float | None:
    match = re.search(r"\d+(?:\.\d+)?", value or "")
    return float(match.group()) if match else None


def impact_factor_value(value: str) -> float | None:
    text = (value or "").strip()
    if not text:
        return None
    if re.search(
        r"\b(?:n/?a|not\s+available|not\s+applicable|not\s+configured|unknown|unmatched|missing|preprint)\b",
        text,
        re.I,
    ):
        return None
    if text in {"—", "-"}:
        return None
    match = re.match(r"^\s*(\d+(?:\.\d+)?)\b", text)
    if not match:
        return None
    value_number = float(match.group(1))
    if value_number >= 100:
        return None
    return value_number


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
        if not any("journal" in header or "platform" in header for header in normalized):
            continue
        if index + 1 >= len(lines) or not re.match(
            r"^\s*\|?\s*:?-+", lines[index + 1]
        ):
            continue
        for row_line in lines[index + 2 :]:
            if not row_line.lstrip().startswith("|"):
                break
            values = split_markdown_row(row_line)
            if len(values) < len(headers):
                values.extend([""] * (len(headers) - len(values)))
            row = dict(zip(headers, values))
            record_type = row.get("Type", "").strip()
            source = row.get(
                "Journal / platform", row.get("Journal", row.get("Platform", ""))
            ).strip()
            doi = normalize_doi(row.get("DOI", ""))
            jif_text = row.get("2025 JIF", row.get("JIF", "")).strip()
            total_text = row.get("Total", "").strip()
            jif = impact_factor_value(jif_text)
            records.append(
                {
                    "inclusion_date": inclusion_date,
                    "article": row.get("Article", "").strip(),
                    "journal": source,
                    "source": source,
                    "record_type": record_type,
                    "doi": doi,
                    "score": numeric_value(total_text),
                    "relevance_score": numeric_value(row.get("Relevance", "")),
                    "ai_score": numeric_value(row.get("AI fit", "")),
                    "life_science_score": numeric_value(
                        row.get("Life-science fit", "")
                    ),
                    "priority_score": numeric_value(row.get("Priority", "")),
                    "publication_score": numeric_value(row.get("Publication", "")),
                    "impact_factor": jif,
                    "impact_factor_label": (
                        str(jif).rstrip("0").rstrip(".")
                        if jif is not None
                        else "Preprint"
                        if record_type.lower() == "preprint"
                        else "NA"
                    ),
                    "published": row.get("Published", "").strip(),
                    "profile": PROFILE,
                    "brief_url": f"/statgen-radar/article.html?date={inclusion_date}",
                }
            )
        break
    return records


def build_journal_index(reports_dir: Path) -> list[dict]:
    """Build the compatible cumulative index from current-profile reports only."""
    by_key: dict[str, dict] = {}
    for report_path in sorted(reports_dir.glob("*.md")):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", report_path.stem):
            continue
        text = report_path.read_text(encoding="utf-8")
        if not is_current_profile(text):
            continue
        for record in parse_inclusion_table(text, report_path.stem):
            key = record["doi"].casefold() if record["doi"] else "|".join(
                re.sub(r"\s+", " ", value).strip().casefold()
                for value in (record["article"], record["source"])
            )
            previous = by_key.get(key)
            if previous is None or record["inclusion_date"] < previous["inclusion_date"]:
                by_key[key] = record
    rows = list(by_key.values())
    rows.sort(
        key=lambda row: (
            -(row["score"] if row["score"] is not None else -1),
            -(row["priority_score"] if row["priority_score"] is not None else -1),
            -(row["relevance_score"] if row["relevance_score"] is not None else -1),
            row["impact_factor"] is None,
            -(row["impact_factor"] if row["impact_factor"] is not None else -1),
            row["journal"].lower(),
            row["article"].lower(),
        )
    )
    return rows


def preserve_legacy_website_report(destination: Path) -> None:
    if not destination.exists():
        return
    previous = destination.read_text(encoding="utf-8")
    if is_current_profile(previous):
        return
    legacy_dir = destination.parent / "legacy"
    legacy_dir.mkdir(parents=True, exist_ok=True)
    legacy_path = legacy_dir / destination.name
    if not legacy_path.exists():
        shutil.copy2(destination, legacy_path)


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
    if not is_current_profile(text):
        raise ValueError(f"Refusing to publish a report without Profile: {PROFILE}")

    reports_dir = website / "statgen-radar" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    destination = reports_dir / report.name
    preserve_legacy_website_report(destination)
    shutil.copy2(report, destination)

    archive_path = website / "data" / "statgen-radar.json"
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    if archive_path.exists():
        archive = json.loads(archive_path.read_text(encoding="utf-8"))
    else:
        archive = []

    journal_articles = parse_value(text, "Journal articles", 0)
    preprints = parse_value(text, "Preprints", 0)
    included = parse_value(text, "Passed threshold", 0)
    if not included and (journal_articles or preprints):
        included = journal_articles + preprints

    item = {
        "date": date,
        "title": f"{DISPLAY_NAME} — Daily Brief",
        "summary": executive_summary(text),
        "records": included,
        "journal_articles": journal_articles,
        "top_journal_articles": journal_articles,
        "preprints": preprints,
        "jif_edition": parse_value(text, "JIF edition", "2025"),
        "profile": PROFILE,
        "url": f"/statgen-radar/article.html?date={date}",
    }

    # Historical StatGen rows remain in report files/Git history but are not
    # part of the current archive or cumulative index.
    archive = [
        row
        for row in archive
        if row.get("profile") == PROFILE and row.get("date") != date
    ]
    archive.append(item)
    archive.sort(key=lambda row: row.get("date", ""), reverse=True)
    archive_path.write_text(
        json.dumps(archive, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    cumulative_index = build_journal_index(reports_dir)
    # Keep the established filename for the deployed frontend. It now contains
    # both exact-whitelist journal articles and arXiv/bioRxiv preprints.
    index_path = website / "data" / "statgen-radar-journals.json"
    index_path.write_text(
        json.dumps(cumulative_index, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"Published {report.name}; archive entries={len(archive)}; "
        f"indexed records={len(cumulative_index)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
