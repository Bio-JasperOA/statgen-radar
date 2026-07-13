#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import html
import re
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import quote

import feedparser
import requests
import yaml
from dateutil import parser as dtparser

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "literature.db"
UA = {"User-Agent": "StatGen-Radar/0.1 (academic literature monitor)"}

RSS_SOURCES = {
    "Nature Genetics": "https://www.nature.com/ng.rss",
    "Nature": "https://www.nature.com/nature.rss",
    "Nature Computational Science": "https://www.nature.com/natcomputsci.rss",
    "Briefings in Bioinformatics": "https://academic.oup.com/rss/site_5488/advanceAccess_3218.xml",
}

@dataclass
class Article:
    source: str
    title: str
    abstract: str
    authors: str
    published: str
    url: str
    doi: str = ""
    score: int = 0
    matched_terms: str = ""

    @property
    def uid(self) -> str:
        basis = self.doi.lower().strip() or normalize(self.title)
        return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html.unescape(text or ""))
    return re.sub(r"\s+", " ", text).strip()


def parse_date(value: str) -> datetime | None:
    try:
        dt = dtparser.parse(value)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def within_days(value: str, days: int) -> bool:
    dt = parse_date(value)
    return not dt or dt >= datetime.now(timezone.utc) - timedelta(days=days)


def load_keywords() -> dict[str, int]:
    with open(ROOT / "config" / "keywords.yml", encoding="utf-8") as handle:
        groups = yaml.safe_load(handle)
    return {term.lower(): int(weight) for group in groups.values() for term, weight in group.items()}


def score_article(article: Article, keywords: dict[str, int]) -> Article:
    text = f"{article.title} {article.abstract}".lower()
    matches = [(term, weight) for term, weight in keywords.items() if term in text]
    article.score = sum(weight for _, weight in matches)
    article.matched_terms = ", ".join(term for term, _ in sorted(matches, key=lambda x: -x[1]))
    return article


def collect_rss(days: int) -> list[Article]:
    rows: list[Article] = []
    for source, url in RSS_SOURCES.items():
        try:
            feed = feedparser.parse(url, request_headers=UA)
            for e in feed.entries:
                published = e.get("published") or e.get("updated") or ""
                if not within_days(published, days):
                    continue
                rows.append(Article(source, clean(e.get("title", "")), clean(e.get("summary", "")),
                                    clean(e.get("author", "")), published, e.get("link", ""),
                                    clean(e.get("prism_doi", "") or e.get("dc_identifier", ""))))
        except Exception as exc:
            print(f"WARN RSS {source}: {exc}", file=sys.stderr)
    return rows


def collect_arxiv(days: int) -> list[Article]:
    query = quote('all:"genome-wide association" OR all:GWAS OR all:"statistical genetics"')
    url = f"https://export.arxiv.org/api/query?search_query={query}&start=0&max_results=100&sortBy=submittedDate&sortOrder=descending"
    try:
        feed = feedparser.parse(requests.get(url, headers=UA, timeout=30).text)
        rows = []
        for e in feed.entries:
            published = e.get("published", "")
            if within_days(published, days):
                rows.append(Article("arXiv", clean(e.title), clean(e.summary),
                                    ", ".join(a.name for a in e.get("authors", [])), published,
                                    e.get("link", "")))
        return rows
    except Exception as exc:
        print(f"WARN arXiv: {exc}", file=sys.stderr)
        return []


def collect_rxiv(server: str, days: int) -> list[Article]:
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=days)
    url = f"https://api.biorxiv.org/details/{server}/{start}/{end}/0"
    rows: list[Article] = []
    try:
        data = requests.get(url, headers=UA, timeout=40).json()
        for item in data.get("collection", []):
            doi = item.get("doi", "")
            rows.append(Article(server, clean(item.get("title", "")), clean(item.get("abstract", "")),
                                clean(item.get("authors", "")), item.get("date", ""),
                                f"https://doi.org/{doi}" if doi else "", doi))
    except Exception as exc:
        print(f"WARN {server}: {exc}", file=sys.stderr)
    return rows


def init_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.execute("""CREATE TABLE IF NOT EXISTS articles (
        uid TEXT PRIMARY KEY, source TEXT, title TEXT, abstract TEXT, authors TEXT,
        published TEXT, url TEXT, doi TEXT, score INTEGER, matched_terms TEXT,
        first_seen TEXT
    )""")
    return con


def save(con: sqlite3.Connection, articles: Iterable[Article]) -> int:
    inserted = 0
    for a in articles:
        cur = con.execute("""INSERT OR IGNORE INTO articles VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (a.uid, a.source, a.title, a.abstract, a.authors, a.published, a.url, a.doi,
             a.score, a.matched_terms, datetime.now(timezone.utc).isoformat()))
        inserted += cur.rowcount
    con.commit()
    return inserted


def report(articles: list[Article], mode: str, days: int) -> Path:
    now = datetime.now(timezone.utc)
    out_dir = ROOT / "reports" / ("weekly" if mode == "weekly" else "daily")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{now.date().isoformat()}.md"
    ranked = sorted(articles, key=lambda a: (-a.score, a.published, a.title))
    lines = [f"# StatGen Radar — {mode.title()} Brief", "",
             f"Generated: {now.isoformat(timespec='minutes')}", f"Window: last {days} day(s)",
             f"Relevant records: {len(ranked)}", "", "## Priority reading", ""]
    if not ranked:
        lines.append("No records met the relevance threshold in this run.")
    for i, a in enumerate(ranked, 1):
        excerpt = a.abstract[:700] + ("…" if len(a.abstract) > 700 else "")
        lines += [f"### {i}. {a.title}", "", f"- **Source:** {a.source}",
                  f"- **Published:** {a.published or 'Unknown'}", f"- **Score:** {a.score}",
                  f"- **Matched terms:** {a.matched_terms or 'None'}",
                  f"- **Authors:** {a.authors or 'Not provided'}", f"- **DOI:** {a.doi or 'Not provided'}",
                  f"- **Link:** {a.url or 'Not provided'}", "", excerpt or "Abstract unavailable.", ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=1)
    p.add_argument("--mode", choices=["daily", "weekly"], default="daily")
    p.add_argument("--min-score", type=int, default=3)
    args = p.parse_args()

    keywords = load_keywords()
    collected = collect_rss(args.days) + collect_arxiv(args.days)
    collected += collect_rxiv("biorxiv", args.days) + collect_rxiv("medrxiv", args.days)
    relevant = [score_article(a, keywords) for a in collected if a.title]
    relevant = [a for a in relevant if a.score >= args.min_score]
    unique = {a.uid: a for a in relevant}
    con = init_db()
    inserted = save(con, unique.values())
    path = report(list(unique.values()), args.mode, args.days)
    print(f"Collected={len(collected)} relevant={len(unique)} inserted={inserted} report={path}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
