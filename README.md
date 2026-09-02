# AI for Life Science Radar

AI for Life Science Radar is an automated literature-monitoring pipeline for
high-quality artificial-intelligence research in the life sciences. It focuses
on Virtual Embryo, developmental dynamics, cell-state transitions, single-cell
and spatial omics, while retaining broader AI-enabled biology such as protein,
genome and drug-discovery models.

The repository and deployed `/statgen-radar/` path keep their established names
for link and automation compatibility. Reports generated with the current
profile contain `Profile: ai-for-life-science`; historical StatGen reports stay
in Git history and report storage but are not included in the current archive or
cumulative index.

## Inclusion policy

Every included record must satisfy all of the following conditions:

1. clear AI-method content above the configured AI threshold in the title and
   first 700 abstract characters;
2. clear life-science content above the independent biology threshold in the
   same title/opening-abstract window;
3. no primary-purpose signal for GWAS, PRS, Mendelian randomization,
   heritability, PheWAS, TWAS, LDSC, eQTL/sQTL or related statistical genetics;
4. either an exact match to the curated top-journal whitelist or a preprint from
   arXiv or bioRxiv; and
5. an original research-like title rather than a correction, editorial, News &
   Views, protocol, review, perspective, commentary or viewpoint; and
6. no clinical imaging/outcome-prediction or routine docking, QSAR, ADMET or
   virtual-screening task in the title/opening abstract.

Statistical-genetics exclusions scan the complete title and abstract, so GWAS,
PRS, MR, heritability, PheWAS, TWAS, LDSC and QTL aims are rejected even when
they occur after the positive-gate window.

Journal Impact Factor is used only to rank and display eligible journal
articles. It never grants eligibility. Daily reports contain at most ten
records. The stricter preprint topic threshold helps control preprint noise.

## Sources

### Exact-whitelist journal literature

- Europe PMC REST API for indexed titles and abstracts;
- Crossref REST API for topic searches and a direct sweep of every configured
  top journal; and
- selected publisher RSS feeds for low-latency Nature Portfolio coverage.

The exact whitelist is stored in `config/journal_metrics.yml`. It covers leading
general, methods, computational-biology, developmental and cell-biology venues,
including Nature, Science, Cell, Nature Methods, Nature Biotechnology, Nature
Machine Intelligence, Nature Computational Science, Cell Systems, Cell
Genomics, Genome Biology, Genome Research, Molecular Systems Biology,
Bioinformatics and PLOS Computational Biology.

### Preprints

- arXiv API, restricted to q-bio.GN/CB/QM/MN/SC/TO; cs.LG, cs.AI and stat.ML
  records additionally require strong life-science context;
- bioRxiv API, restricted to Bioinformatics, Genomics, Developmental Biology,
  Cell Biology, Bioengineering, Synthetic Biology and Systems Biology;
  Molecular Biology and Biophysics additionally require a strong AI signal.

All preprints are subsequently filtered locally with both hard topic gates.

medRxiv is intentionally excluded from this profile.

## Scoring and ranking

`config/keywords.yml` contains four transparent groups:

- `ai` supplies the required AI-fit score;
- `life_science` supplies the independent life-science-fit score;
- `current_priority` raises Virtual Embryo, developmental, single-cell and
  spatial-omics records within the eligible set; and
- `excluded_primary_purpose`, `excluded_content_prefixes` and
  `excluded_domain_noise` remove out-of-scope aims, non-original content and
  clinical/routine-computational noise.

Short terms such as `AI`, `VAE`, `RNA` and `DNA` are matched on normalized token
boundaries rather than arbitrary substrings. Ranking uses topic relevance,
current-project priority, publication score and JIF in that order. The complete
JIF table remains in `config/journal_metrics_2025.tsv`.

## Quick start

```bash
python -m pip install -r requirements.txt
python run_ranked_with_tsv.py --days 1 --mode daily
python add_inclusion_table.py --mode daily
```

Reports are written to `reports/daily/` or `reports/weekly/`. The established
SQLite path, `data/literature.db`, is retained for compatibility. If a new-profile
run would replace a same-date historical report, the old file is first copied to
`reports/legacy/`.

Useful overrides are available for testing:

```bash
python run_ranked_with_tsv.py --days 7 --mode weekly
python run_ranked_with_tsv.py --days 1 --mode daily --min-ai-score 6
python run_ranked_with_tsv.py --days 1 --mode daily --max-records 5
```

Daily mode always clamps the output to ten records, even if a larger
`--max-records` value is supplied.

## Automated runs

`.github/workflows/radar.yml` runs daily at 23:55 UTC (07:55 the next day in
Asia/Shanghai) and weekly at 00:15 UTC each Monday (08:15 Asia/Shanghai). Daily
runs publish the report, compatible archive JSON and compatible cumulative-index
JSON to `Bio-JasperOA/Bio-JasperOA.github.io`.

The website keeps the existing data paths:

- `data/statgen-radar.json` for current-profile daily briefs;
- `data/statgen-radar-journals.json` for the cumulative index, now containing
  both top-journal papers and arXiv/bioRxiv preprints.

## Limitations

- Crossref records may lack abstracts, so a relevant item can score below the
  gates until richer metadata appears through RSS or Europe PMC.
- Keyword gates are deterministic and auditable but do not replace reading the
  paper; ambiguous AI terminology can still require manual review.
- Preprint metadata and versions can change, and a preprint-to-journal pair may
  not share a DOI or identical title.
- Publisher feeds and publication dates can lag or disagree across sources.
