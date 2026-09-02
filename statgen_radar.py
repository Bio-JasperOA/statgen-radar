#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import html
import re
import shutil
import sqlite3
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Iterable
from urllib.parse import quote

import feedparser
import requests
import yaml
from dateutil import parser as dtparser

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "literature.db"
KEYWORDS_PATH = ROOT / "config" / "keywords.yml"
METRICS_PATH = ROOT / "config" / "journal_metrics.yml"
PROFILE = "ai-for-life-science"
DISPLAY_NAME = "AI for Life Science Radar"
UA = {"User-Agent": "AI-Life-Science-Radar/1.0 (academic literature monitor; contact via GitHub)"}

# RSS is a low-latency supplement. Crossref and Europe PMC provide the wider
# formal-journal coverage, so an unavailable feed does not stop a run.
RSS_SOURCES = {
    "Nature": "https://www.nature.com/nature.rss",
    "Nature Methods": "https://www.nature.com/nmeth.rss",
    "Nature Biotechnology": "https://www.nature.com/nbt.rss",
    "Nature Machine Intelligence": "https://www.nature.com/natmachintell.rss",
    "Nature Computational Science": "https://www.nature.com/natcomputsci.rss",
    "Nature Biomedical Engineering": "https://www.nature.com/natbiomedeng.rss",
    "Nature Genetics": "https://www.nature.com/ng.rss",
    "Nature Cell Biology": "https://www.nature.com/ncb.rss",
    "Nature Communications": "https://www.nature.com/ncomms.rss",
    "Genome Research": "https://genome.cshlp.org/rss/current.xml",
}

# Retrieval is deliberately broader than final inclusion. Every candidate is
# later required to pass independent AI and life-science gates.
SEARCH_TERMS = [
    '"artificial intelligence"',
    '"machine learning"',
    '"deep learning"',
    '"foundation model"',
    '"large language model"',
    '"generative model"',
    '"transformer model"',
    'transformer',
    'pretrained',
    'pretraining',
    'LLM',
    'GNN',
    'embedding',
    '"diffusion model"',
    '"variational autoencoder"',
    '"flow matching"',
    '"graph neural network"',
    '"representation learning"',
    '"self-supervised learning"',
    '"multimodal learning"',
    '"virtual cell"',
    '"virtual embryo"',
    '"perturbation prediction"',
]

CROSSREF_TOPIC_QUERIES = [
    "foundation model single-cell spatial transcriptomics",
    "machine learning developmental biology embryo cell fate",
    "deep learning genomics transcriptomics proteomics",
    "generative model protein RNA drug discovery",
    "virtual cell virtual embryo biological simulation",
    "multimodal learning biomedical life science",
    "graph neural network molecular cell biology",
]

# The pipeline intentionally does not collect medRxiv. A formal item must be
# in the exact top-journal whitelist; a preprint must come from one of these
# two platforms.
PREPRINT_SOURCES = {"arXiv", "bioRxiv"}

ARXIV_QBIO_CATEGORIES = {
    "q-bio.GN",
    "q-bio.CB",
    "q-bio.QM",
    "q-bio.MN",
    "q-bio.SC",
    "q-bio.TO",
}
ARXIV_ML_CATEGORIES = {"cs.LG", "cs.AI", "stat.ML"}
BIORXIV_STANDARD_CATEGORIES = {
    "bioinformatics",
    "genomics",
    "developmental biology",
    "cell biology",
    "bioengineering",
    "synthetic biology",
    "systems biology",
}
BIORXIV_STRICT_AI_CATEGORIES = {"molecular biology", "biophysics"}

# Broad computer-science categories are admitted only when the title/opening
# abstract is unmistakably about living systems. Conversely, broad bioRxiv
# Molecular Biology/Biophysics records must carry a strong AI-method signal.
STRONG_LIFE_CONTEXT_TERMS = (
    "single cell",
    "spatial transcriptomics",
    "spatial omics",
    "cell biology",
    "developmental biology",
    "cell fate",
    "cell lineage",
    "morphogenesis",
    "organogenesis",
    "embryo",
    "embryogenesis",
    "stem cell",
    "organoid",
    "genomics",
    "transcriptomics",
    "proteomics",
    "epigenomics",
    "gene expression",
    "protein",
    "rna",
    "dna",
    "drug discovery",
)
STRONG_AI_CONTEXT_TERMS = (
    "artificial intelligence",
    "machine learning",
    "deep learning",
    "foundation model",
    "large language model",
    "llm",
    "transformer",
    "diffusion model",
    "variational autoencoder",
    "vae",
    "graph neural network",
    "gnn",
    "generative model",
    "flow matching",
    "neural ode",
    "self supervised learning",
    "representation learning",
    "joint embedding",
)


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
    ai_score: int = 0
    life_science_score: int = 0
    priority_score: int = 0
    matched_ai_terms: str = ""
    matched_life_science_terms: str = ""
    matched_priority_terms: str = ""
    exclusion_terms: str = ""
    topic_eligible: bool = False
    excluded_primary_purpose: bool = False
    excluded_content_type: bool = False
    excluded_domain_noise: bool = False
    categories: str = ""

    @property
    def uid(self) -> str:
        basis = normalize_doi(self.doi) or normalize(self.title)
        return hashlib.sha256(basis.encode("utf-8")).hexdigest()

    @property
    def record_type(self) -> str:
        return "Preprint" if self.source in PREPRINT_SOURCES else "Journal article"

    @property
    def journal(self) -> str:
        return self.source.split(" / ", 1)[1] if " / " in self.source else self.source


def normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


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
    if dt is None:
        return False
    start = datetime.now(timezone.utc).date() - timedelta(days=max(days, 0))
    return dt.astimezone(timezone.utc).date() >= start


@lru_cache(maxsize=1)
def top_journal_lookup() -> dict[str, str]:
    with METRICS_PATH.open(encoding="utf-8") as handle:
        config = yaml.safe_load(handle) or {}
    lookup: dict[str, str] = {}
    for item in config.get("top_journals") or []:
        if isinstance(item, str):
            canonical, aliases = item, []
        else:
            canonical = str(item.get("name") or "").strip()
            aliases = item.get("aliases") or []
        if not canonical:
            continue
        for value in [canonical, *aliases]:
            lookup[normalize(str(value))] = canonical
    if not lookup:
        raise ValueError("config/journal_metrics.yml contains no top_journals whitelist")
    return lookup


def top_journal_names() -> list[str]:
    return list(dict.fromkeys(top_journal_lookup().values()))


def canonical_top_journal(name: str) -> str | None:
    return top_journal_lookup().get(normalize(name))


def is_top_journal(name: str) -> bool:
    return canonical_top_journal(name) is not None


# Compatibility name used by older helper code.
def is_priority_journal(name: str) -> bool:
    return is_top_journal(name)


def is_allowed_source(article: Article) -> bool:
    if article.record_type == "Preprint":
        return article.source in PREPRINT_SOURCES
    return is_top_journal(article.journal)


def load_keywords() -> dict:
    with KEYWORDS_PATH.open(encoding="utf-8") as handle:
        config = yaml.safe_load(handle) or {}
    if config.get("profile") != PROFILE:
        raise ValueError(f"Expected keyword profile {PROFILE!r}")
    for group_name in ("ai", "life_science", "current_priority"):
        group = config.get(group_name)
        if not isinstance(group, dict) or not group:
            raise ValueError(f"Keyword group {group_name!r} is missing or empty")
        config[group_name] = {str(term): int(weight) for term, weight in group.items()}
    config["excluded_primary_purpose"] = [
        str(term) for term in config.get("excluded_primary_purpose") or []
    ]
    config["excluded_content_prefixes"] = [
        str(term) for term in config.get("excluded_content_prefixes") or []
    ]
    config["excluded_domain_noise"] = [
        str(term) for term in config.get("excluded_domain_noise") or []
    ]
    config["thresholds"] = {
        key: int(value) for key, value in (config.get("thresholds") or {}).items()
    }
    return config


def term_in_normalized_text(term: str, normalized_text: str) -> bool:
    normalized_term = normalize(term)
    if not normalized_term:
        return False
    return f" {normalized_term} " in f" {normalized_text} "


def score_group(normalized_text: str, terms: dict[str, int]) -> tuple[int, list[tuple[str, int]]]:
    matches = [
        (term, weight)
        for term, weight in terms.items()
        if term_in_normalized_text(term, normalized_text)
    ]
    return sum(weight for _, weight in matches), sorted(matches, key=lambda row: (-row[1], row[0]))


def contains_any(text: str, terms: Iterable[str]) -> bool:
    normalized_text = normalize(text)
    return any(term_in_normalized_text(term, normalized_text) for term in terms)


def arxiv_category_allowed(
    categories: Iterable[str], title: str, abstract: str
) -> bool:
    category_set = {str(category).strip() for category in categories if category}
    if category_set & ARXIV_QBIO_CATEGORIES:
        return True
    if category_set & ARXIV_ML_CATEGORIES:
        return contains_any(
            f"{title} {abstract[:700]}", STRONG_LIFE_CONTEXT_TERMS
        )
    return False


def biorxiv_category_allowed(category: str, title: str, abstract: str) -> bool:
    normalized_category = normalize(category)
    if normalized_category in BIORXIV_STANDARD_CATEGORIES:
        return True
    if normalized_category in BIORXIV_STRICT_AI_CATEGORIES:
        return contains_any(
            f"{title} {abstract[:700]}", STRONG_AI_CONTEXT_TERMS
        )
    return False


def score_article(article: Article, keyword_config: dict) -> Article:
    # Both positive gates use only the title and opening abstract so incidental
    # method/background mentions later in a long abstract cannot confer fit.
    normalized_gate = normalize(f"{article.title} {article.abstract[:700]}")
    # Statistical-genetics exclusions deliberately scan the complete abstract:
    # a primary GWAS/PRS/MR aim must not evade screening by appearing late.
    normalized_full = normalize(f"{article.title} {article.abstract}")

    article.ai_score, ai_matches = score_group(normalized_gate, keyword_config["ai"])
    article.life_science_score, life_matches = score_group(
        normalized_gate, keyword_config["life_science"]
    )
    article.priority_score, priority_matches = score_group(
        normalized_gate, keyword_config["current_priority"]
    )
    exclusions = [
        term
        for term in keyword_config.get("excluded_primary_purpose", [])
        if term_in_normalized_text(term, normalized_full)
    ]
    domain_exclusions = [
        term
        for term in keyword_config.get("excluded_domain_noise", [])
        if term_in_normalized_text(term, normalized_gate)
    ]
    normalized_title = normalize(article.title)
    content_prefixes = [
        prefix
        for prefix in keyword_config.get("excluded_content_prefixes", [])
        if normalized_title == normalize(prefix)
        or normalized_title.startswith(f"{normalize(prefix)} ")
    ]

    article.score = article.ai_score + article.life_science_score + article.priority_score
    article.matched_ai_terms = ", ".join(term for term, _ in ai_matches)
    article.matched_life_science_terms = ", ".join(term for term, _ in life_matches)
    article.matched_priority_terms = ", ".join(term for term, _ in priority_matches)
    article.exclusion_terms = ", ".join(
        [*content_prefixes, *domain_exclusions, *exclusions]
    )
    article.excluded_primary_purpose = bool(exclusions)
    article.excluded_content_type = bool(content_prefixes)
    article.excluded_domain_noise = bool(domain_exclusions)
    combined_matches = [*ai_matches, *life_matches, *priority_matches]
    article.matched_terms = ", ".join(
        term for term, _ in sorted(combined_matches, key=lambda row: (-row[1], row[0]))
    )

    thresholds = keyword_config.get("thresholds") or {}
    required_total = (
        thresholds.get("preprint_topic_total", 12)
        if article.record_type == "Preprint"
        else thresholds.get("topic_total", 10)
    )
    article.topic_eligible = (
        article.ai_score >= thresholds.get("ai", 5)
        and article.life_science_score >= thresholds.get("life_science", 4)
        and article.score >= required_total
        and not article.excluded_primary_purpose
        and not article.excluded_content_type
        and not article.excluded_domain_noise
    )
    return article


def collect_rss(days: int) -> list[Article]:
    rows: list[Article] = []
    for source, url in RSS_SOURCES.items():
        try:
            feed = feedparser.parse(url, request_headers=UA)
            if getattr(feed, "bozo", False) and not feed.entries:
                raise RuntimeError(str(getattr(feed, "bozo_exception", "invalid feed")))
            for entry in feed.entries:
                published = entry.get("published") or entry.get("updated") or ""
                if not within_days(published, days):
                    continue
                doi = clean(entry.get("prism_doi", "") or entry.get("dc_identifier", ""))
                rows.append(
                    Article(
                        source,
                        clean(entry.get("title", "")),
                        clean(entry.get("summary", "")),
                        clean(entry.get("author", "")),
                        published,
                        entry.get("link", ""),
                        normalize_doi(doi),
                    )
                )
        except Exception as exc:
            print(f"WARN RSS {source}: {exc}", file=sys.stderr)
    return rows


def collect_arxiv(days: int) -> list[Article]:
    ai_query = (
        'all:"artificial intelligence" OR all:"machine learning" OR '
        'all:"deep learning" OR all:"foundation model" OR '
        'all:"generative model" OR all:transformer OR all:pretrained OR '
        'all:pretraining OR all:LLM OR all:GNN OR all:embedding OR '
        'all:"diffusion model" OR all:"graph neural network" OR '
        'all:"self-supervised learning" OR all:"virtual cell" OR '
        'all:"virtual embryo" OR all:"variational autoencoder" OR '
        'all:"flow matching"'
    )
    biology_query = (
        'all:biology OR all:genomics OR all:transcriptomics OR '
        'all:proteomics OR all:"single-cell" OR all:"spatial transcriptomics" OR '
        'all:embryo OR all:"stem cell" OR all:protein OR all:RNA OR '
        'all:"drug discovery"'
    )
    category_query = " OR ".join(
        f"cat:{category}"
        for category in sorted(ARXIV_QBIO_CATEGORIES | ARXIV_ML_CATEGORIES)
    )
    query = quote(f"({category_query}) AND ({ai_query}) AND ({biology_query})")
    url = (
        "https://export.arxiv.org/api/query?"
        f"search_query={query}&start=0&max_results=300&sortBy=submittedDate&sortOrder=descending"
    )
    try:
        response = requests.get(url, headers=UA, timeout=40)
        response.raise_for_status()
        feed = feedparser.parse(response.text)
        rows: list[Article] = []
        for entry in feed.entries:
            if not within_days(entry.get("published", ""), days):
                continue
            categories = [
                clean(tag.get("term", ""))
                for tag in entry.get("tags", [])
                if tag.get("term")
            ]
            title, abstract = clean(entry.title), clean(entry.summary)
            if not arxiv_category_allowed(categories, title, abstract):
                continue
            rows.append(Article(
                "arXiv",
                title,
                abstract,
                ", ".join(author.name for author in entry.get("authors", [])),
                entry.get("published", ""),
                entry.get("link", ""),
                categories=", ".join(categories),
            ))
        return rows
    except Exception as exc:
        print(f"WARN arXiv: {exc}", file=sys.stderr)
        return []


def collect_rxiv(server: str, days: int) -> list[Article]:
    if server != "biorxiv":
        raise ValueError("Only bioRxiv is enabled for the AI for Life Science profile")
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=max(days, 0))
    rows: list[Article] = []
    cursor = 0
    while cursor < 2000:
        url = f"https://api.biorxiv.org/details/{server}/{start}/{end}/{cursor}"
        try:
            response = requests.get(url, headers=UA, timeout=40)
            response.raise_for_status()
            payload = response.json()
            collection = payload.get("collection", [])
            for item in collection:
                title = clean(item.get("title", ""))
                abstract = clean(item.get("abstract", ""))
                category = clean(item.get("category", ""))
                if not biorxiv_category_allowed(category, title, abstract):
                    continue
                doi = normalize_doi(item.get("doi", ""))
                rows.append(
                    Article(
                        "bioRxiv",
                        title,
                        abstract,
                        clean(item.get("authors", "")),
                        item.get("date", ""),
                        f"https://doi.org/{doi}" if doi else "",
                        doi,
                        categories=category,
                    )
                )
            if not collection:
                break
            cursor += len(collection)
            messages = payload.get("messages") or []
            try:
                total = int(messages[0].get("total", cursor)) if messages else cursor
            except (TypeError, ValueError):
                total = cursor
            if cursor >= total:
                break
        except Exception as exc:
            print(f"WARN {server}: {exc}", file=sys.stderr)
            break
    return rows


def collect_europe_pmc(days: int) -> list[Article]:
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=max(days, 0))
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
        for item in response.json().get("resultList", {}).get("result", []):
            title = clean(item.get("title", ""))
            if not title:
                continue
            doi = normalize_doi(item.get("doi", ""))
            pmid, pmcid = clean(item.get("pmid", "")), clean(item.get("pmcid", ""))
            journal = clean(item.get("journalTitle", ""))
            published = (
                item.get("firstPublicationDate")
                or item.get("electronicPublicationDate")
                or item.get("journalInfo", {}).get("printPublicationDate")
                or ""
            )
            url = (
                f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
                if pmid
                else f"https://europepmc.org/article/PMC/{pmcid}"
                if pmcid
                else f"https://doi.org/{doi}"
                if doi
                else ""
            )
            rows.append(
                Article(
                    f"Europe PMC / {journal}" if journal else "Europe PMC",
                    title,
                    clean(item.get("abstractText", "")),
                    clean(item.get("authorString", "")),
                    published,
                    url,
                    doi,
                )
            )
    except Exception as exc:
        print(f"WARN Europe PMC: {exc}", file=sys.stderr)
    return rows


def crossref_date(item: dict) -> str:
    for field in ("published-online", "published-print", "published", "issued", "created"):
        parts = item.get(field, {}).get("date-parts", [])
        if parts and parts[0]:
            values = parts[0]
            return "-".join([str(values[0]), *(f"{value:02d}" for value in values[1:3])])
    return ""


def article_from_crossref(item: dict) -> Article | None:
    title_values = item.get("title", [])
    title = clean(title_values[0] if title_values else "")
    if not title:
        return None
    doi = normalize_doi(item.get("DOI", ""))
    authors = ", ".join(
        clean(" ".join(filter(None, [author.get("given", ""), author.get("family", "")])))
        for author in item.get("author", [])
    )
    container = item.get("container-title", [])
    journal = clean(container[0] if container else "")
    return Article(
        f"Crossref / {journal}" if journal else "Crossref",
        title,
        clean(item.get("abstract", "")),
        authors,
        crossref_date(item),
        item.get("URL", "") or (f"https://doi.org/{doi}" if doi else ""),
        doi,
    )


def crossref_request(params: dict, label: str) -> list[Article]:
    rows: list[Article] = []
    try:
        response = requests.get(
            "https://api.crossref.org/works", params=params, headers=UA, timeout=60
        )
        response.raise_for_status()
        for item in response.json().get("message", {}).get("items", []):
            article = article_from_crossref(item)
            if article:
                rows.append(article)
    except Exception as exc:
        print(f"WARN Crossref ({label}): {exc}", file=sys.stderr)
    return rows


def collect_crossref(days: int) -> list[Article]:
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=max(days, 0))
    select = (
        "DOI,title,abstract,author,published-online,published-print,"
        "published,issued,created,URL,container-title"
    )
    rows: list[Article] = []
    for query_text in CROSSREF_TOPIC_QUERIES:
        rows.extend(
            crossref_request(
                {
                    "query.bibliographic": query_text,
                    "filter": f"from-pub-date:{start},until-pub-date:{end},type:journal-article",
                    "sort": "published",
                    "order": "desc",
                    "rows": 300,
                    "select": select,
                },
                query_text,
            )
        )
    return rows


def collect_priority_journals(days: int) -> list[Article]:
    """Sweep each configured top journal and verify the result by exact name."""
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=max(days, 0))
    select = (
        "DOI,title,abstract,author,published-online,published-print,"
        "published,issued,created,URL,container-title"
    )
    rows: list[Article] = []
    for journal_query in top_journal_names():
        candidates = crossref_request(
            {
                "query.container-title": journal_query,
                "filter": f"from-pub-date:{start},until-pub-date:{end},type:journal-article",
                "sort": "published",
                "order": "desc",
                "rows": 300,
                "select": select,
            },
            f"top journal {journal_query}",
        )
        rows.extend(article for article in candidates if is_top_journal(article.journal))
    return rows


def init_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    # Preserve the existing path and schema for compatibility with prior runs.
    connection.execute(
        """CREATE TABLE IF NOT EXISTS articles (
        uid TEXT PRIMARY KEY, source TEXT, title TEXT, abstract TEXT, authors TEXT,
        published TEXT, url TEXT, doi TEXT, score INTEGER, matched_terms TEXT,
        first_seen TEXT
    )"""
    )
    return connection


def save(connection: sqlite3.Connection, articles: Iterable[Article]) -> int:
    inserted = 0
    for article in articles:
        cursor = connection.execute(
            """INSERT OR IGNORE INTO articles VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                article.uid,
                article.source,
                article.title,
                article.abstract,
                article.authors,
                article.published,
                article.url,
                article.doi,
                article.score,
                article.matched_terms,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        inserted += cursor.rowcount
    connection.commit()
    return inserted


def deduplicate(articles: Iterable[Article]) -> list[Article]:
    unique: dict[str, Article] = {}
    for article in articles:
        current = unique.get(article.uid)
        if current is None:
            unique[article.uid] = article
            continue

        def quality(row: Article) -> tuple:
            source_rank = 2 if row.record_type == "Journal article" and is_top_journal(row.journal) else (
                1 if row.record_type == "Preprint" else 0
            )
            return source_rank, bool(row.abstract), len(row.abstract), bool(row.doi)

        if quality(article) > quality(current):
            unique[article.uid] = article
    return list(unique.values())


def topic_sort_key(article: Article) -> tuple:
    return (
        -article.score,
        -article.priority_score,
        -(article.ai_score + article.life_science_score),
        article.published,
        article.title,
    )


def preserve_legacy_report(path: Path, mode: str) -> None:
    if not path.exists():
        return
    previous = path.read_text(encoding="utf-8")
    if f"Profile: {PROFILE}" in previous:
        return
    legacy_dir = ROOT / "reports" / "legacy" / mode
    legacy_dir.mkdir(parents=True, exist_ok=True)
    legacy_path = legacy_dir / path.name
    if not legacy_path.exists():
        shutil.copy2(path, legacy_path)


def report(articles: list[Article], mode: str, days: int) -> Path:
    now = datetime.now(timezone.utc)
    out_dir = ROOT / "reports" / ("weekly" if mode == "weekly" else "daily")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{now.date().isoformat()}.md"
    preserve_legacy_report(path, mode)
    ranked = sorted(articles, key=topic_sort_key)
    type_counts = Counter(article.record_type for article in ranked)
    source_counts = Counter(article.source.split(" / ", 1)[0] for article in ranked)
    lines = [
        f"# {DISPLAY_NAME} — {mode.title()} Brief",
        "",
        f"Profile: {PROFILE}",
        f"Generated: {now.isoformat(timespec='minutes')}",
        f"Window: last {days} day(s)",
        f"Relevant records: {len(ranked)}",
        f"Journal articles: {type_counts.get('Journal article', 0)}",
        f"Preprints: {type_counts.get('Preprint', 0)}",
        "",
        "## Source coverage",
        "",
    ]
    lines.extend(f"- **{source}:** {count}" for source, count in source_counts.most_common())
    if not source_counts:
        lines.append("No source returned a record above both topic gates.")
    lines += ["", "## Priority reading", ""]
    if not ranked:
        lines.append("No records met both AI and life-science thresholds in this run.")
    for index, article in enumerate(ranked, 1):
        excerpt = article.abstract[:700] + ("…" if len(article.abstract) > 700 else "")
        lines += [
            f"### {index}. {article.title}",
            "",
            f"- **Record type:** {article.record_type}",
            f"- **Journal / platform:** {article.journal}",
            *([f"- **Category:** {article.categories}"] if article.categories else []),
            f"- **Published:** {article.published or 'Unknown'}",
            f"- **Relevance score:** {article.score}",
            f"- **AI fit:** {article.ai_score}",
            f"- **Life-science fit:** {article.life_science_score}",
            f"- **Priority fit:** {article.priority_score}",
            f"- **Matched terms:** {article.matched_terms or 'None'}",
            f"- **Authors:** {article.authors or 'Not provided'}",
            f"- **DOI:** {article.doi or 'Not provided'}",
            f"- **Link:** {article.url or 'Not provided'}",
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
    parser.add_argument("--min-score", type=int, default=None)
    args = parser.parse_args()

    keyword_config = load_keywords()
    collectors = {
        "RSS": collect_rss(args.days),
        "Top journals": collect_priority_journals(args.days),
        "Europe PMC": collect_europe_pmc(args.days),
        "Crossref": collect_crossref(args.days),
        "arXiv": collect_arxiv(args.days),
        "bioRxiv": collect_rxiv("biorxiv", args.days),
    }
    for name, records in collectors.items():
        print(f"SOURCE {name}: collected={len(records)}")

    collected = [article for records in collectors.values() for article in records]
    scored = [score_article(article, keyword_config) for article in collected if article.title]
    unique = deduplicate(scored)
    retained = [
        article
        for article in unique
        if article.topic_eligible and is_allowed_source(article)
    ]
    limit = keyword_config["thresholds"].get(
        "daily_limit" if args.mode == "daily" else "weekly_limit",
        10 if args.mode == "daily" else 30,
    )
    retained = sorted(retained, key=topic_sort_key)[:limit]

    connection = init_db()
    inserted = save(connection, retained)
    path = report(retained, args.mode, args.days)
    print(
        f"Collected={len(collected)} candidates={len(unique)} retained={len(retained)} "
        f"journal={sum(article.record_type == 'Journal article' for article in retained)} "
        f"preprint={sum(article.record_type == 'Preprint' for article in retained)} "
        f"inserted={inserted} report={path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
