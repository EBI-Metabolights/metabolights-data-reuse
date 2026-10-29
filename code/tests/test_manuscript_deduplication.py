"""Offline checks for publication matching, collector options and review consistency.

Use constructed records and simulated responses to test missing identifiers,
different DOI versions, repeated dataset links and the MTBLS-only command
line. These tests do not perform a live literature search.
"""
from __future__ import annotations

import importlib.util
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
import runpy
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import pandas as pd
from collect_study_metadata import SESSION, fetch_mw_ids, get_mtbls_metadata
from generate_mtbls_reuse_analysis_outputs import validate_publication_subcategories


ROOT = Path(__file__).resolve().parents[1]
HELPER_PATH = ROOT / "repo_counts_from_review_sheet_corrected_study_ids.py"
COLLECTOR_PATH = ROOT / "main_mtbls_mw_gnps_reuse.py"
SPEC = importlib.util.spec_from_file_location("manuscript_counts", HELPER_PATH)
assert SPEC and SPEC.loader
MANUSCRIPT_COUNTS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MANUSCRIPT_COUNTS)
COLLECTOR_SPEC = importlib.util.spec_from_file_location("reuse_collector", COLLECTOR_PATH)
assert COLLECTOR_SPEC and COLLECTOR_SPEC.loader
COLLECTOR = importlib.util.module_from_spec(COLLECTOR_SPEC)
COLLECTOR_SPEC.loader.exec_module(COLLECTOR)


class ManuscriptDeduplicationTests(unittest.TestCase):
    def test_reference_filter_retains_records_without_a_doi(self) -> None:
        for missing in [float('nan'), None, pd.NA, '']:
            with self.subTest(missing=repr(missing)):
                records = pd.DataFrame([{
                    'Study ID': 'MTBLS1',
                    'Match.DOI': missing,
                    'MTBLS.PublicationDOIs': missing,
                }])
                self.assertEqual(len(COLLECTOR.exclude_primary_publications(records)), 1)

    def test_reference_filter_ignores_missing_reference_dois(self) -> None:
        for missing in [float('nan'), None, pd.NA, '']:
            with self.subTest(missing=repr(missing)):
                records = pd.DataFrame([{
                    'Study ID': 'MTBLS1',
                    'Match.DOI': '10.1234/reuse',
                    'MTBLS.PublicationDOIs': missing,
                }])
                self.assertEqual(len(COLLECTOR.exclude_primary_publications(records)), 1)

    def test_reference_filter_preserves_matching_for_recorded_dois(self) -> None:
        records = pd.DataFrame([
            {'Study ID': 'MTBLS1', 'Match.DOI': '10.1234/Reference',
             'MTBLS.PublicationDOIs': '10.1234/reference'},
            {'Study ID': 'MTBLS1', 'Match.DOI': '10.1234/reuse',
             'MTBLS.PublicationDOIs': '10.1234/reference'},
            {'Study ID': 'MTBLS2', 'Match.DOI': ' 10.1234/TARGET ',
             'MTBLS.PublicationDOIs': '10.1234/other; 10.1234/target'},
            {'Study ID': 'MTBLS3', 'Match.DOI': '10.1234/retained',
             'MTBLS.PublicationDOIs': ''},
        ])
        actual = COLLECTOR.exclude_primary_publications(records)
        pd.testing.assert_frame_equal(actual, records.iloc[[1, 3]].reset_index(drop=True))

    def test_workbench_text_response_recognises_complete_accessions(self) -> None:
        examples = [
            ('ST000001 ST000002', ['ST000001', 'ST000002']),
            ('ST000002\nST000001 ST000002', ['ST000001', 'ST000002']),
            ('ST0000017 fooST000002bar', []),
            ('No dataset identifiers', []),
        ]
        for text, expected in examples:
            with self.subTest(text=text):
                response = Mock(status_code=200, text=text)
                response.json.side_effect = ValueError('Example text response')
                with patch.object(SESSION, 'get', return_value=response):
                    self.assertEqual(fetch_mw_ids(), expected)

    def test_cli_help_exits_before_network_or_collection(self) -> None:
        output = StringIO()
        with patch.object(sys, 'argv', [str(COLLECTOR_PATH), '--help']), \
                patch('requests.Session.request', side_effect=AssertionError('Help must not use the network')) as request, \
                patch('collect_study_metadata.fetch_mtbls_ids', side_effect=AssertionError('Help must not discover IDs')) as discovery, \
                redirect_stdout(output):
            with self.assertRaises(SystemExit) as exited:
                runpy.run_path(str(COLLECTOR_PATH), run_name='__main__')
        self.assertEqual(exited.exception.code, 0)
        self.assertIn('--limit_mtbls', output.getvalue())
        self.assertIn('--per_id_limit', output.getvalue())
        self.assertIn('--basename', output.getvalue())
        request.assert_not_called()
        discovery.assert_not_called()

    def test_cli_passes_explicit_options_to_collection(self) -> None:
        with patch.object(COLLECTOR, 'main_collect_all', return_value='search.xlsx') as collect:
            result = COLLECTOR.main(['--limit_mtbls', '3', '--per_id_limit', '7', '--basename', 'sample'])
        self.assertEqual(result, 'search.xlsx')
        collect.assert_called_once_with(limit_mtbls=3, per_id_limit=7, basename='sample')

    def test_cli_preserves_existing_unlimited_study_default(self) -> None:
        with patch.object(COLLECTOR, 'main_collect_all', return_value='search.xlsx') as collect:
            COLLECTOR.main([])
        collect.assert_called_once_with(limit_mtbls=None, per_id_limit=None, basename='mtbls_mw_gnps_reuse')

    def test_cli_rejects_invalid_limits_and_disabled_repositories_before_collection(self) -> None:
        for option, value in [('--limit_mtbls', '0'), ('--limit_mtbls', '-1'),
                              ('--per_id_limit', '0'), ('--per_id_limit', '-1'),
                              ('--limit_mtbls', 'invalid'), ('--limit_mw', '1'), ('--limit_gnps', '1')]:
            with self.subTest(option=option, value=value), \
                    patch.object(COLLECTOR, 'main_collect_all') as collect, redirect_stderr(StringIO()):
                with self.assertRaises(SystemExit) as exited:
                    COLLECTOR.main([option, value])
                self.assertEqual(exited.exception.code, 2)
                collect.assert_not_called()

    def test_mtbls_only_collection_does_not_query_other_repositories(self) -> None:
        metadata = {"MTBLS1": {"MTBLS.Accession": "MTBLS1"}}
        collectors = {
            "collect_europe_pmc": "EuropePMC",
            "collect_springer_rss": "Springer",
            "collect_wiley_search_rss": "Wiley",
            "collect_sciencedirect_api": "ScienceDirectAPI",
            "collect_pmc_esearch": "PMC",
        }
        with ExitStack() as stack:
            stack.enter_context(patch.object(COLLECTOR, "fetch_mtbls_ids", return_value=["MTBLS1", "MTBLS2"]))
            mw = stack.enter_context(patch.object(COLLECTOR, "fetch_mw_ids", create=True, side_effect=AssertionError("MW must not be queried")))
            gnps = stack.enter_context(patch.object(COLLECTOR, "fetch_gnps_ids", create=True, side_effect=AssertionError("GNPS must not be queried")))
            get_metadata = stack.enter_context(patch.object(COLLECTOR, "get_study_metadata", return_value=metadata))
            searches = []
            for function, source in collectors.items():
                frame = pd.DataFrame({
                    "Source": [source], "Study ID": ["MTBLS1"],
                    "Match.Title": ["Example publication"], "Match.DOI": ["10.1234/example"],
                })
                searches.append(stack.enter_context(patch.object(COLLECTOR, function, return_value=frame)))
            stack.enter_context(patch.object(COLLECTOR, "enrich_springer_rows_with_bibliography", side_effect=lambda frame: frame))
            stack.enter_context(patch.object(pd.DataFrame, "to_excel"))
            stack.enter_context(patch.object(COLLECTOR.Path, "mkdir"))
            export = stack.enter_context(patch.object(COLLECTOR, "export_all", return_value="search.xlsx"))

            result = COLLECTOR.main_collect_all(limit_mtbls=1, limit_mw=1, limit_gnps=1, per_id_limit=7)

            self.assertEqual(result, "search.xlsx")
            mw.assert_not_called()
            gnps.assert_not_called()
            get_metadata.assert_called_once_with(["MTBLS1"])
            for search in searches:
                search.assert_called_once()
                self.assertEqual(search.call_args.kwargs["study_ids"], ["MTBLS1"])
                self.assertEqual(search.call_args.kwargs["study_metadata"], metadata)
                self.assertEqual(search.call_args.kwargs["per_id_limit"], 7)
            self.assertEqual(export.call_args.kwargs["mtbls_metadata_df"]["MTBLS.Accession"].tolist(), ["MTBLS1"])

    def test_conflicting_confirmed_subcategories_require_manual_resolution(self) -> None:
        expanded = pd.DataFrame({
            "Publication ID": ["doi:10.1234/paper"] * 2,
            "StudyIDs_split": ["MTBLS1"] * 2,
            "Reviewer.Notes": ["Reuse", "Benchmark"],
            "Subcategories (free text)": ["Analysis Workflow", "Machine Learning"],
        })
        with self.assertRaisesRegex(ValueError, "doi:10.1234/paper.*Analysis Workflow.*Machine Learning"):
            validate_publication_subcategories(expanded)

    def test_excluded_subcategory_does_not_override_confirmed_assignment(self) -> None:
        expanded = pd.DataFrame({
            "Publication ID": ["doi:10.1234/paper"] * 3,
            "Reviewer.Notes": ["Reuse", "Benchmark", "Duplicate"],
            "Subcategories (free text)": ["Analysis Workflow", "Analysis Workflow", "Machine Learning"],
        })
        validate_publication_subcategories(expanded)

    def test_different_publications_can_have_different_subcategories(self) -> None:
        expanded = pd.DataFrame({
            "Publication ID": ["doi:10.1234/paper1", "doi:10.1234/paper2"],
            "Reviewer.Notes": ["Reuse", "Reuse"],
            "Subcategories (free text)": ["Analysis Workflow", "Machine Learning"],
        })
        validate_publication_subcategories(expanded)

    def test_multiple_subcategories_in_one_confirmed_cell_require_resolution(self) -> None:
        expanded = pd.DataFrame({
            "Publication ID": ["doi:10.1234/paper"],
            "Reviewer.Notes": ["Reuse"],
            "Subcategories (free text)": ["Analysis Workflow; Machine Learning"],
        })
        with self.assertRaisesRegex(ValueError, "Conflicting confirmed subcategories"):
            validate_publication_subcategories(expanded)

    def test_mtbls_metadata_keeps_assay_platforms(self) -> None:
        response = Mock(status_code=200)
        response.json.return_value = {
            "isaInvestigation": {"studies": [{
                "assays": [{
                    "measurementType": {"annotationValue": "metabolite profiling"},
                    "technologyType": {"annotationValue": "mass spectrometry"},
                    "technologyPlatform": "LC-MS",
                }]
            }]}
        }
        with patch.object(SESSION, "get", return_value=response):
            metadata = get_mtbls_metadata("MTBLS42")
        self.assertEqual(metadata["MTBLS.Platforms"], "LC-MS")

    def test_collector_exports_full_metadata_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            search = pd.DataFrame({"Study ID": ["MTBLS42"], "Match.Title": ["Paper"]})
            metadata = pd.DataFrame({
                "MTBLS.Accession": ["MTBLS42", "MTBLS43"],
                "MTBLS.ReleaseDate": ["2025-01-01", "2025-02-01"],
            })
            output = COLLECTOR.export_all(
                basename=str(Path(temp_dir) / "search"),
                combined_df=search,
                mtbls_metadata_df=metadata,
            )
            with pd.ExcelFile(output) as workbook:
                self.assertIn("MTBLS_metadata", workbook.sheet_names)
                pd.testing.assert_frame_equal(
                    pd.read_excel(workbook, sheet_name="MTBLS_metadata"), metadata
                )

    def test_doi_missing_records_use_normalized_title_year_and_study(self) -> None:
        records = pd.DataFrame(
            {
                "Source": ["EuropePMC", "PMC", "EuropePMC", "PMC"],
                "Match.DOI": ["", "", "", ""],
                "Match.Title": ["Same paper.", "Same paper", "Different paper", "Another paper"],
                "Match.Year": [2022, 2022, 2022, 2022],
                "Study ID": ["MTBLS17", "MTBLS17", "MTBLS17", "MTBLS17"],
            }
        )

        result = COLLECTOR.dedupe_combined(records)

        self.assertEqual(3, len(result))
        self.assertEqual(["PMC", "EuropePMC", "PMC"], result["Source"].tolist())
        self.assertEqual(
            ["Another paper", "Different paper", "Same paper"], result["Match.Title"].tolist()
        )

    def test_current_notes_column_prefers_confirmed_journal_record(self) -> None:
        expanded = pd.DataFrame(
            {
                "Title": ["Paper", "Paper"],
                "StudyIDs_split": ["MTBLS78", "MTBLS78"],
                "Journal": ["bioRxiv", "Journal publication"],
                "Reviewer.Notes": ["Published preprint", "Reuse"],
            }
        )

        result = MANUSCRIPT_COUNTS.deduplicate_publication_study_prefer_confirmed(expanded)

        self.assertEqual(1, len(result))
        self.assertEqual("Journal publication", result.iloc[0]["Journal"])
        self.assertEqual("Reuse", result.iloc[0]["Reviewer.Notes"])

    def test_distinct_dois_are_not_merged_even_for_same_title_and_study(self) -> None:
        expanded = pd.DataFrame(
            {
                "Title": ["Data Treatment for LC-MS Untargeted Analysis"] * 2,
                "DOI": ["10.1007/978-1-0716-4334-1_5", "10.1007/978-1-4939-7643-0_3"],
                "PMCID": ["", ""],
                "StudyIDs_split": ["MTBLS59", "MTBLS59"],
                "Reviewer.Notes": ["Reuse", "Reuse"],
            }
        )

        result = MANUSCRIPT_COUNTS.deduplicate_publication_study_prefer_confirmed(expanded)

        self.assertEqual(2, len(result))
        self.assertEqual(2, result["Publication ID"].nunique())

    def test_preprint_and_journal_article_stay_separate_for_manual_review(self) -> None:
        expanded = pd.DataFrame(
            {
                "Title": ["Same paper", "Same paper"],
                "DOI": ["10.1101/preprint", "10.1234/journal"],
                "PMCID": ["PMC123", "PMC123"],
                "StudyIDs_split": ["MTBLS78", "MTBLS78"],
                "Reviewer.Notes": ["Published preprint", "Reuse"],
            }
        )

        result = MANUSCRIPT_COUNTS.deduplicate_publication_study_prefer_confirmed(expanded)

        self.assertEqual(2, len(result))
        self.assertEqual({"Published preprint", "Reuse"}, set(result["Reviewer.Notes"]))

    def test_doi_missing_record_links_only_to_unique_pmcid_doi(self) -> None:
        expanded = pd.DataFrame(
            {
                "Title": ["Paper.", "Paper"],
                "DOI": ["10.1234/article", ""],
                "PMCID": ["PMC456", "PMC456"],
                "StudyIDs_split": ["MTBLS374", "MTBLS374"],
                "Reviewer.Notes": ["Reuse", "Reuse"],
            }
        )

        result = MANUSCRIPT_COUNTS.deduplicate_publication_study_prefer_confirmed(expanded)

        self.assertEqual(1, len(result))
        self.assertEqual("doi:10.1234/article", result.iloc[0]["Publication ID"])

    def test_ambiguous_pmcid_requires_manual_resolution(self) -> None:
        expanded = pd.DataFrame(
            {
                "Title": ["Paper", "Paper", "Paper"],
                "DOI": ["10.1234/version1", "10.1234/version2", ""],
                "PMCID": ["PMC789", "PMC789", "PMC789"],
                "StudyIDs_split": ["MTBLS1"] * 3,
            }
        )

        with self.assertRaisesRegex(ValueError, "ambiguous PMCID"):
            MANUSCRIPT_COUNTS.add_publication_ids(expanded)


if __name__ == "__main__":
    unittest.main()
