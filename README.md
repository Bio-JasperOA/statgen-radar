# StatGen Radar

StatGen Radar is an automated literature-monitoring pipeline for GWAS and statistical genetics. It collects journal articles and preprints, applies configurable relevance scoring, deduplicates records in SQLite, and generates Markdown briefs.

## Sources

### Formal journal literature

- Europe PMC REST API, covering PubMed- and PMC-indexed journal articles
- Crossref REST API, used to supplement newly published and online-first journal records that may not yet be indexed in PubMed
- Nature Genetics RSS
- Nature RSS
- Nature Computational Science RSS
- Briefings in Bioinformatics RSS

### Preprints

- arXiv API
- bioRxiv API
- medRxiv API

A failing or unavailable source is logged and does not stop the remaining collectors. Europe PMC is the primary formal-publication source; Crossref and journal RSS feeds provide complementary early-online coverage.

## Quick start

```bash
python -m pip install -r requirements.txt
python statgen_radar.py --days 7 --mode daily
```

Reports are written to `reports/daily/` or `reports/weekly/`. The SQLite database is stored at `data/literature.db`.

## Commands

```bash
python statgen_radar.py --days 1 --mode daily
python statgen_radar.py --days 7 --mode weekly
python statgen_radar.py --days 30 --mode daily --min-score 4
```

## Automated runs

`.github/workflows/radar.yml` runs daily at 00:00 UTC, corresponding to 08:00 in Asia/Taipei, and commits newly generated reports and database updates back to the repository. Weekly mode runs every Monday.

You can also run it manually from the GitHub Actions page.

## Report contents

Each report now includes:

- total relevant records;
- counts of formal journal articles and preprints;
- counts by source family;
- record type, journal/source, publication date, score, matched terms, DOI and link for each paper.

When duplicate records are returned by several sources, DOI-based and normalized-title matching are used. Formal journal versions and records with richer abstracts are preferred over preprint or metadata-only records.

## Relevance model

The current version uses transparent weighted keyword scoring rather than an external LLM. Edit `config/keywords.yml` to change terms and weights. Both title and abstract are scored. A later version can add semantic classification and structured summaries without changing the collectors or database schema.

## Important limitations

- Europe PMC indexing may lag behind a publisher's online publication date.
- Crossref records often contain incomplete or missing abstracts, which can lower keyword scores.
- RSS addresses can change and are therefore isolated in configuration.
- Some publisher feeds expose titles but incomplete abstracts.
- A preprint and its journal version may not always share identifiers; DOI-based and normalized-title deduplication are both used.
- Publication dates differ across online-first, issue, PubMed indexing and repository deposition dates.
- Generated summaries are extractive metadata briefs, not substitutes for reading the paper.

## Repository structure

```text
config/keywords.yml
statgen_radar.py
requirements.txt
.github/workflows/radar.yml
data/.gitkeep
reports/daily/.gitkeep
reports/weekly/.gitkeep
```
