#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import html
import re
import sqlite3
import sys
from collections import Counter
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
UA = {"User-Agent": "StatGen-Radar/0.2 (academic literature monitor; contact via GitHub)"}

RSS_SOURCES = {
    "Nature Genetics": "https://www.nature.com/ng.rss",
    "Nature": "https://www.nature.com/nature.rss",
    "Nature Computational Science": "https://www.nature.com/natcomputsci.rss",
    "Briefings in Bioinformatics": "https://academic.oup.com/rss/site_5488/advanceAccess_3218.xml",
}

SEARCH_TERMS = [
    '"genome-wide association"',
    "GWAS",
    '"statistical genetics"',
    '"Mendelian randomization"',
    '"genetic correlation"',
    '"polygenic risk score"',
    '"fine-mapping"',
    "colocalization",
]

PREPRINT_SOURCES = {"arXiv", "biorxiv", "medrxiv"}


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
        basis = normalize_doi(self.doi) or normalize(self.title)
        return hashlib.sha256(basis.encode("utf-8")).hexdigest()

    @property
    def record_type(self) -> str:
        return "Preprint" if self.source in PREPRINT_SOURCES else "Journal article"


def normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def normalize_doi(value: str) -> str:
    value = clean(value).lower()
    value = re.sub(r"^(https?://(dx\.)?doi\.org/|doi:\s*)", "", value)
    return value.strip().rstrip(".")


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
            if getattr(feed, "bozo", False) and not feed.entries:
                raise RuntimeError(str(getattr(feed, "bozo_exception", "invalid feed")))
            for e in feed.entries:
                published = e.get("published") or e.get("updated") or ""
                if not within_days(published, days):
                    continue
                doi = clean(e.get("prism_doi", "") or e.get("dc_identifier", ""))
                rows.append(Article(
                    source,
                    clean(e.get("title", "")),
                    clean(e.get("summary", "")),
                    clean(e.get("author", "")),
                    published,
                    e.get("link", ""),
                    normalize_doi(doi),
                ))
        except Exception as exc:
            print(f"WARN RSS {source}: {exc}", file=sys.stderr)
    return rows


def collect_arxiv(days: int) -> list[Article]:
    query = quote('all:"genome-wide association" OR all:GWAS OR all:"statistical genetics"')
    url = (
        "https://export.arxiv.org/api/query?"
        f"search_query={query}&start=0&max_results=100&sortBy=submittedDate&sortOrder=descending"
    )
    try:
        response = requests.get(url, headers=UA, timeout=30)
        response.raise_for_status()
        feed = feedparser.parse(response.text)
        rows = []
        for e in feed.entries:
            published = e.get("published", "")
            if within_days(published, days):
                rows.append(Article(
                    "arXiv",
                    clean(e.title),
                    clean(e.summary),
                    ", ".join(a.name for a in e.get("authors", [])),
                    published,
                    e.get("link", ""),
                ))
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
        response = requests.get(url, headers=UA, timeout=40)
        response.raise_for_status()
        data = response.json()
        for item in data.get("collection", []):
            doi = normalize_doi(item.get("doi", ""))
            rows.append(Article(
                server,
                clean(item.get("title", "")),
                clean(item.get("abstract", "")),
                clean(item.get("authors", "")),
                item.get("date", ""),
                f"https://doi.org/{doi}" if doi else "",
                doi,
            ))
    except Exception as exc:
        print(f"WARN {server}: {exc}", file=sys.stderr)
    return rows


def collect_europe_pmc(days: int) -> list[Article]:
    """Collect indexed journal articles from PubMed/PMC via Europe PMC."""
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=days)
    topic_query = " OR ".join(f"TITLE_ABS:{term}" for term in SEARCH_TERMS)
    query = f"({topic_query}) AND FIRST_PDATE:[{start} TO {end}] AND (SRC:MED OR SRC:PMC)"
    params = {
        "query": query,
        "format": "json",
        "resultType": "core",
        "pageSize": 1000,
        "sort": "FIRST_PDATE_D",
    }
    rows: list[Article] = []
    try:
        response = requests.get(
            "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
            params=params,
            headers=UA,
            timeout=60,
        )
        response.raise_for_status()
        data = response.json()
        for item in data.get("resultList", {}).get("result", []):
            title = clean(item.get("title", ""))
            if not title:
                continue
            doi = normalize_doi(item.get("doi", ""))
            pmid = clean(item.get("pmid", ""))
            pmcid = clean(item.get("pmcid", ""))
            journal = clean(item.get("journalTitle", ""))
            source = f"Europe PMC / {journal}" if journal else "Europe PMC"
            published = (
                item.get("firstPublicationDate")
                or item.get("electronicPublicationDate")
                or item.get("journalInfo", {}).get("printPublicationDate")
                or ""
            )
            if pmid:
                url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
            elif pmcid:
                url = f"https://europepmc.org/article/PMC/{pmcid}"
            elif doi:
                url = f"https://doi.org/{doi}"
            else:
                url = ""
            rows.append(Article(
                source,
                title,
                clean(item.get("abstractText", "")),
                clean(item.get("authorString", "")),
                published,
                url,
                doi,
            ))
    except Exception as exc:
        print(f"WARN Europe PMC: {exc}", file=sys.stderr)
    return rows


def crossref_date(item: dict) -> str:
    for field in ("published-online", "published-print", "published", "issued", "created"):
        parts = item.get(field, {}).get("date-parts", [])
        if parts and parts[0]:
            values = parts[0]
            return "-".join([str(values[0]), *(f"{v:02d}" for v in values[1:3])])
    return ""


def collect_crossref(days: int) -> list[Article]:
    """Supplement online-first journal records that may not yet be indexed in PubMed."""
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=days)
    rows: list[Article] = []
    seen: set[str] = set()
    queries = [
        "genome-wide association GWAS statistical genetics",
        "Mendelian randomization genetic correlation polygenic risk score",
        "fine-mapping colocalization genetic association",
    ]
    for query_text in queries:
        params = {
            "query.bibliographic": query_text,
            "filter": f"from-pub-date:{start},until-pub-date:{end},type:journal-article",
            "sort": "published",
            "order": "desc",
            "rows": 200,
            "select": "DOI,title,abstract,author,published-online,published-print,published,issued,created,URL,container-title",
        }
        try:
            response = requests.get(
                "https://api.crossref.org/works",
                params=params,
                headers=UA,
                timeout=60,
            )
            response.raise_for_status()
            for item in response.json().get("message", {}).get("items", []):
                title_values = item.get("title", [])
                title = clean(title_values[0] if title_values else "")
                if not title:
                    continue
                doi = normalize_doi(item.get("DOI", ""))
                key = doi or normalize(title)
                if key in seen:
                    continue
                seen.add(key)
                authors = ", ".join(
                    clean(" ".join(filter(None, [a.get("given", ""), a.get("family", "")])))
                    for a in item.get("author", [])
                )
                container = item.get("container-title", [])
                journal = clean(container[0] if container else "")
                source = f"Crossref / {journal}" if journal else "Crossref"
                rows.append(Article(
                    source,
                    title,
                    clean(item.get("abstract", "")),
                    authors,
                    crossref_date(item),
                    item.get("URL", "") or (f"https://doi.org/{doi}" if doi else ""),
                    doi,
                ))
        except Exception as exc:
            print(f"WARN Crossref ({query_text}): {exc}", file=sys.stderr)
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
        cur = con.execute(
            """INSERT OR IGNORE INTO articles VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                a.uid, a.source, a.title, a.abstract, a.authors, a.published, a.url, a.doi,
                a.score, a.matched_terms, datetime.now(timezone.utc).isoformat(),
            ),
        )
        inserted += cur.rowcount
    con.commit()
    return inserted


def deduplicate(articles: Iterable[Article]) -> list[Article]:
    """Prefer journal records and richer metadata when sources overlap."""
    unique: dict[str, Article] = {}
    for article in articles:
        key = article.uid
        current = unique.get(key)
        if current is None:
            unique[key] = article
            continue
        current_quality = (
            current.record_type == "Journal article",
            bool(current.abstract),
            len(current.abstract),
            bool(current.doi),
        )
        new_quality = (
            article.record_type == "Journal article",
            bool(article.abstract),
            len(article.abstract),
            bool(article.doi),
        )
        if new_quality > current_quality:
            unique[key] = article
    return list(unique.values())


def report(articles: list[Article], mode: str, days: int) -> Path:
    now = datetime.now(timezone.utc)
    out_dir = ROOT / "reports" / ("weekly" if mode == "weekly" else "daily")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{now.date().isoformat()}.md"
    ranked = sorted(articles, key=lambda a: (-a.score, a.published, a.title))
    type_counts = Counter(a.record_type for a in ranked)
    source_counts = Counter(a.source.split(" / ", 1)[0] for a in ranked)
    lines = [
        f"# StatGen Radar — {mode.title()} Brief",
        "",
        f"Generated: {now.isoformat(timespec='minutes')}",
        f"Window: last {days} day(s)",
        f"Relevant records: {len(ranked)}",
        f"Journal articles: {type_counts.get('Journal article', 0)}",
        f"Preprints: {type_counts.get('Preprint', 0)}",
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
    for i, a in enumerate(ranked, 1):
        excerpt = a.abstract[:700] + ("…" if len(a.abstract) > 700 else "")
        lines += [
            f"### {i}. {a.title}",
            "",
            f"- **Record type:** {a.record_type}",
            f"- **Source:** {a.source}",
            f"- **Published:** {a.published or 'Unknown'}",
            f"- **Score:** {a.score}",
            f"- **Matched terms:** {a.matched_terms or 'None'}",
            f"- **Authors:** {a.authors or 'Not provided'}",
            f"- **DOI:** {a.doi or 'Not provided'}",
            f"- **Link:** {a.url or 'Not provided'}",
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
    parser.add_argument("--min-score", type=int, default=3)
    args = parser.parse_args()

    keywords = load_keywords()
    collectors = {
        "RSS": collect_rss(args.days),
        "Europe PMC": collect_europe_pmc(args.days),
        "Crossref": collect_crossref(args.days),
        "arXiv": collect_arxiv(args.days),
        "bioRxiv": collect_rxiv("biorxiv", args.days),
        "medRxiv": collect_rxiv("medrxiv", args.days),
    }
    for name, records in collectors.items():
        print(f"SOURCE {name}: collected={len(records)}")

    collected = [article for records in collectors.values() for article in records]
    scored = [score_article(a, keywords) for a in collected if a.title]
    relevant = [a for a in scored if a.score >= args.min_score]
    unique = deduplicate(relevant)

    con = init_db()
    inserted = save(con, unique)
    path = report(unique, args.mode, args.days)
    print(
        f"Collected={len(collected)} relevant={len(unique)} "
        f"journal={sum(a.record_type == 'Journal article' for a in unique)} "
        f"preprint={sum(a.record_type == 'Preprint' for a in unique)} "
        f"inserted={inserted} report={path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
