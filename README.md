# StatGen Radar

StatGen Radar is an automated literature-monitoring pipeline for GWAS and statistical genetics. It collects papers and preprints, applies configurable relevance scoring, deduplicates records in SQLite, and generates Markdown briefs.

## Sources

- arXiv API
- bioRxiv API
- medRxiv API
- Nature Genetics RSS
- Nature RSS
- Nature Computational Science RSS
- Briefings in Bioinformatics RSS

A failing or unavailable source is logged and does not stop the remaining collectors.

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

## Relevance model

The first version uses transparent weighted keyword scoring rather than an external LLM. Edit `config/keywords.yml` to change terms and weights. A later version can add OpenAI-based structured summaries through an optional repository secret without changing the collectors or database schema.

## Important limitations

- RSS addresses can change and are therefore isolated in configuration.
- Some publisher feeds expose titles but incomplete abstracts.
- A preprint and its journal version may not always share identifiers; DOI-based and normalized-title deduplication are both used.
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
