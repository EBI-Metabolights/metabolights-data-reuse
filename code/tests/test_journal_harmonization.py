"""Offline checks for equivalent publication-source names and result comparisons.

Verify the explicit name mapping and support for older metrics filenames
using small temporary workbooks. No external bibliographic service is queried.
"""

from __future__ import annotations

import unittest
import tempfile
from pathlib import Path

import pandas as pd

from generate_mtbls_reuse_analysis_outputs import JOURNAL_HARMONIZATION, write_comparison


class JournalHarmonizationTests(unittest.TestCase):
    def test_historical_journal_metric_compares_as_publication_sources(self) -> None:
        for name in ['mtbls_reuse_manuscript_metrics.xlsx', 'mtbls_reuse_manuscript_numbers.xlsx']:
            with self.subTest(existing_filename=name):
                self._check_historical_journal_metric(name)

    def _check_historical_journal_metric(self, existing_filename: str) -> None:
        matches = pd.DataFrame({"Title": ["Example publication"], "StudyIDs_split": ["MTBLS1"]})
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with pd.ExcelWriter(root / existing_filename) as writer:
                pd.DataFrame({"Metric": ["Distinct_journals_confirmed_reuse"], "Value": [129]}).to_excel(
                    writer, sheet_name="manuscript_numbers", index=False
                )
                matches.to_excel(writer, sheet_name="df_confirmed_mtbls_matches", index=False)
            comparison = write_comparison(root, {
                "manuscript_numbers": pd.DataFrame({
                    "Metric": ["Distinct_publication_sources_confirmed_reuse"], "Value": [129],
                }),
                "df_confirmed_mtbls_matches": matches,
            }, root)
        self.assertEqual(len(comparison), 1)
        self.assertEqual(comparison.iloc[0]["Delta"], 0)
        self.assertEqual(comparison.iloc[0]["Metric"], "Distinct_publication_sources_confirmed_reuse")

    def test_comparison_prefers_canonical_metrics_when_legacy_copy_exists(self) -> None:
        matches = pd.DataFrame({"Title": ["Example publication"], "StudyIDs_split": ["MTBLS1"]})
        metrics = pd.DataFrame({"Metric": ["Example_count"], "Value": [1]})
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, value in [('mtbls_reuse_manuscript_metrics.xlsx', 1),
                                ('mtbls_reuse_manuscript_numbers.xlsx', 99)]:
                with pd.ExcelWriter(root / name) as writer:
                    metrics.assign(Value=value).to_excel(writer, sheet_name='manuscript_numbers', index=False)
                    matches.assign(Title='Example publication' if value == 1 else 'Stale publication').to_excel(
                        writer, sheet_name='df_confirmed_mtbls_matches', index=False
                    )
            comparison = write_comparison(root, {
                'manuscript_numbers': metrics, 'df_confirmed_mtbls_matches': matches,
            }, root)
            self.assertEqual(comparison['Delta'].tolist(), [0])
            audit = pd.read_excel(root / 'comparison_to_existing_outputs.xlsx', sheet_name=None)
            self.assertTrue(audit['new_title_study_keys'].empty)
            self.assertTrue(audit['removed_title_study_keys'].empty)

    def test_inspected_equivalent_labels_have_canonical_titles(self) -> None:
        expected = {
            "Analytical chemistry": "Analytical Chemistry",
            "American journal of cancer research": "American Journal of Cancer Research",
            "Bioinformatics (Oxford, England)": "Bioinformatics",
            "Journal of proteome research": "Journal of Proteome Research",
            "Nature methods": "Nature Methods",
            "Nature protocols": "Nature Protocols",
            "Scientific reports": "Scientific Reports",
            "Metabolomics : Official journal of the Metabolomic Society": "Metabolomics",
            "PLoS ONE": "PLOS ONE",
            "PLoS computational biology": "PLoS Computational Biology",
            "Wellcome open research": "Wellcome Open Research",
        }
        self.assertEqual(expected, JOURNAL_HARMONIZATION)


if __name__ == "__main__":
    unittest.main()
