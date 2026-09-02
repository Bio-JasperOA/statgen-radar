from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

import statgen_radar as radar


ROOT = Path(__file__).resolve().parents[1]
PUBLISH_SPEC = importlib.util.spec_from_file_location(
    "publish_to_website", ROOT / "scripts" / "publish_to_website.py"
)
publish = importlib.util.module_from_spec(PUBLISH_SPEC)
assert PUBLISH_SPEC.loader is not None
PUBLISH_SPEC.loader.exec_module(publish)


def article(title: str, abstract: str, source: str = "arXiv") -> radar.Article:
    return radar.Article(source, title, abstract, "Test Author", "2026-09-02", "")


class ProfileRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.keywords = radar.load_keywords()

    def test_scgpt_style_preprint_is_included(self) -> None:
        record = article(
            "scGPT: a foundation model for single-cell biology",
            "Generative pre-training of a transformer learns joint embeddings "
            "from single-cell transcriptomics and gene-expression profiles.",
        )
        radar.score_article(record, self.keywords)
        self.assertTrue(
            radar.arxiv_category_allowed(
                ["cs.LG"], record.title, record.abstract
            )
        )
        self.assertTrue(record.topic_eligible)

    def test_clinical_imaging_prediction_is_excluded(self) -> None:
        record = article(
            "Deep learning for MRI survival prediction",
            "A transformer integrates single-cell transcriptomics with radiology "
            "for clinical outcome and survival prediction.",
            "Nature Methods",
        )
        radar.score_article(record, self.keywords)
        self.assertGreaterEqual(record.ai_score, self.keywords["thresholds"]["ai"])
        self.assertGreaterEqual(
            record.life_science_score, self.keywords["thresholds"]["life_science"]
        )
        self.assertTrue(record.excluded_domain_noise)
        self.assertFalse(record.topic_eligible)

    def test_review_prefix_is_excluded(self) -> None:
        record = article(
            "Review: foundation models for single-cell biology",
            "Deep learning and transformer models for single-cell transcriptomics.",
            "Genome Research",
        )
        radar.score_article(record, self.keywords)
        self.assertTrue(record.excluded_content_type)
        self.assertFalse(record.topic_eligible)

    def test_statgen_term_after_gate_window_is_excluded(self) -> None:
        abstract = (
            "A foundation model uses a transformer for single-cell transcriptomics "
            "and developmental biology. "
            + ("Mechanistic validation supports cell-state modelling. " * 20)
            + "The primary downstream analysis is a genome-wide association study."
        )
        self.assertGreater(len(abstract), 700)
        record = article("A virtual cell foundation model", abstract)
        radar.score_article(record, self.keywords)
        self.assertTrue(record.excluded_primary_purpose)
        self.assertFalse(record.topic_eligible)

    def test_preprint_category_guards(self) -> None:
        self.assertTrue(
            radar.arxiv_category_allowed(
                ["q-bio.GN"], "A method", "No additional context"
            )
        )
        self.assertFalse(
            radar.arxiv_category_allowed(
                ["cs.LG"], "A general benchmark", "Image classification"
            )
        )
        self.assertTrue(
            radar.biorxiv_category_allowed(
                "Molecular Biology",
                "A transformer model",
                "Deep learning for proteins",
            )
        )
        self.assertFalse(
            radar.biorxiv_category_allowed(
                "Molecular Biology", "A receptor assay", "Wet-lab measurements"
            )
        )

    def test_exact_journal_whitelist_swaps(self) -> None:
        self.assertTrue(radar.is_top_journal("Genome Research"))
        self.assertTrue(radar.is_top_journal("Molecular Systems Biology"))
        self.assertFalse(radar.is_top_journal("Nature Medicine"))
        self.assertFalse(radar.is_top_journal("Briefings in Bioinformatics"))


class PublishingRegressionTests(unittest.TestCase):
    def test_no_doi_preprints_remain_independent(self) -> None:
        fixture = """# AI for Life Science Radar — Daily Brief

Profile: ai-for-life-science

## Full inclusion table

| No. | Article | Type | Journal / platform | JIF | Published | Relevance | AI fit | Life-science fit | Priority | Publication | Total | DOI |
|---:|---|---|---|---:|---|---:|---:|---:|---:|---:|---:|---|
| 1 | scGPT \\| cell atlas | Preprint | arXiv | Preprint | 2026-09-02 | 30 | 16 | 10 | 4 | 4 | 34 | Not provided |
| 2 | Protein transformer | Preprint | arXiv | Preprint | 2026-09-02 | 25 | 12 | 8 | 0 | 4 | 29 | — |
"""
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "2026-09-02.md"
            report.write_text(fixture, encoding="utf-8")
            rows = publish.build_journal_index(Path(directory))
        self.assertEqual(len(rows), 2)
        self.assertEqual({row["doi"] for row in rows}, {""})
        self.assertIn("scGPT | cell atlas", {row["article"] for row in rows})

    def test_doi_sentinels_and_markdown_pipe(self) -> None:
        for value in (
            "Not provided",
            "Unavailable",
            "NA",
            "N/A",
            "—",
            "-",
            "None",
            "Unknown",
        ):
            self.assertEqual(publish.normalize_doi(value), "")
        self.assertEqual(
            publish.split_markdown_row("| one \\| two | three |"),
            ["one | two", "three"],
        )


if __name__ == "__main__":
    unittest.main()
