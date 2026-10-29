# MTBLS reuse public data and code package

This package accompanies *Revealing the Hidden Landscape of Public Metabolomics Data Reuse in MetaboLights*. The scientific data and results cover MetaboLights (MTBLS) accessions and are supplied as Excel workbooks.

The underlying literature collectors can also look up Metabolomics Workbench and MassIVE/GNPS dataset identifiers. The approach can be extended to other datasets with searchable identifiers; see [Look up datasets from other repositories](#look-up-datasets-from-other-repositories).

## Contents

| File or directory | Contents |
|---|---|
| `data/mtbls_reuse_search_records.xlsx` | Filtered January 2026 search results, with five source tabs and the Combined candidate sheet |
| `data/mtbls_reuse_reviewed_records.xlsx` | 1,239 candidate rows, manual screening decisions and classifications, and a saved copy of the dataset metadata |
| `data/mtbls_study_metadata_original.xlsx` | Original MetaboLights metadata export, with one accession per column, for the 2,560-accession list |
| `data/review_retrieval_provenance.xlsx` | Source-tab and Combined-sheet evidence for each reviewed candidate row |
| `results/mtbls_review_expanded_publication_study_records.xlsx` | Publication–dataset links before repeated pairs are removed |
| `results/mtbls_review_deduplicated_publication_study_records.xlsx` | One row per distinct publication–dataset pair |
| `results/mtbls_reuse_manuscript_metrics.xlsx` | Manuscript metrics, screening outcomes, counts and analysis tables in 20 sheets |
| `figures/` | 16 individual analysis plot panels |
| `manuscript_figures/` | Six assembled main figures and three supplementary figures |
| `code/` | Literature collectors, manual review workbook preparation, analysis, plotting, dependencies and tests |
| `LICENSE` | Apache License 2.0, matching the source project's declared license |
| `CITATION.cff` | Citation metadata using the study title and author list |

The archived scientific data and results are Excel files. Earlier preparation logs and the duplicate JSON metrics table are not included. The blank configuration template uses JSON. Rerunning the code can generate new JSON audit and result files locally.

## Analysis totals

| Metric | Value |
|---|---:|
| Reviewed candidate rows | 1,239 |
| Publication–dataset links before repeats are removed | 1,510 |
| Distinct publication–dataset associations | 1,234 |
| Unique publications before screening | 884 |
| Unique MTBLS accessions mentioned | 896 |
| Confirmed reuse publications | 274 |
| Confirmed publication–dataset associations | 469 |
| MTBLS accessions with confirmed reuse | 273 |
| Accessions reused once / 2–4 times / at least 5 times | 184 / 73 / 16 |
| Publication sources after equivalent names are combined | 129 |
| Excluded publications | 610 |

The exclusions are 553 submission references, 26 mention-only publications, 14 manually labelled published preprints, 8 wrong citations, 4 without accessible full text, 2 labelled as duplicates, 2 false positives and 1 correction or erratum. Publication sources include journals, books and preprint servers.

## Search, review and counting rules

The search workbook is a filtered, analysis-ready copy of the January 2026 MTBLS-identifier search, covering publications dated through 2025. It contains 4,268 retained source-tab hits: PMC 1,715; Europe PMC 1,800; Springer 332; ScienceDirect 298; Wiley 123. The released metadata inventory contains 2,560 distinct MTBLS accessions. The saved metadata export supplies the review metadata and the platform column added to Combined; this step makes no new API request.

The Combined sheet in the search workbook and the Review sheet in the reviewed workbook each retain the same 1,239 candidate rows. Some rows describe the same publication and MTBLS accession found through different literature sources. Applying the released collector's current combination rule to the archived Combined sheet would retain 1,209 rows, removing 30 repeated rows: 29 matched by DOI and accession, and one matched by cleaned title, publication year and accession when no DOI was recorded. The archived workbooks preserve the rows used for manual review.

Two examples from the released workbooks illustrate this distinction. Excel row numbers below include the header row.

1. **Same publication and accession, with a DOI.** Rows 871 and 872 of both Combined and Review describe "The Risa R/Bioconductor package: integrative data analysis from experimental metadata and back again" and accession `MTBLS2`. PMC records DOI `10.1186/1471-2105-15-S1-S11`, while Europe PMC records `10.1186/1471-2105-15-s1-s11`. These are the same DOI despite differences in capitalisation. The Review decisions are `Reuse` and `Duplicate`, respectively. Analysis retains one confirmed publication–MTBLS2 association, preferring the `Reuse` decision.

2. **Same publication and accession, without a recorded DOI.** Rows 1231 and 1239 of both sheets describe the 2022 article "RNA methylation regulators contribute to poor prognosis of hepatocellular carcinoma associated with the suppression of bile acid metabolism: a multi-omics analysis" and accession `MTBLS17`, retrieved through Europe PMC and PMC, respectively. Neither row records a DOI. Their titles differ only by a final full stop, so the collector's cleaned-title/year/accession rule treats them as the same candidate. Both also record PMCID `PMC9360234` and the review decision `Reanalysis`; the analysis uses that shared PMCID to retain one publication–MTBLS17 association.

The 30 repeated candidate rows therefore do not represent 30 additional unique publications. Repeated publication–accession pairs are counted once after manual screening, so these extra rows do not increase the final publication or association totals. Use the supplied reviewed workbook to reproduce the published analysis. A new collection run can produce a different candidate set because the current combination rule removes these repeats and live source records and indexing can change.

During new searches, automatic reference-paper filtering compares only recorded DOI values. A candidate without a DOI is retained for manual screening; two missing DOI cells are not treated as evidence of a reference-paper match. The supplied reviewed workbook remains the input for reproducing the published results.

A DOI is a persistent publication identifier; a PMCID identifies an article in PubMed Central. Publications are matched by DOI when available. A record without a DOI links to a DOI only when its PMCID is associated with exactly one DOI in the reviewed records; otherwise the PMCID itself is used, then the title after simple text cleanup if both identifiers are absent. Different DOIs, including preprint and journal versions, remain separate, with inclusion determined by manual screening. When several rows describe the same publication and accession, a confirmed decision is retained ahead of an excluded decision. Each confirmed publication has one primary category, one sub-category and one scientific domain; the code does not predict these manual labels.

Publication summaries count each confirmed publication once. Dataset summaries retain distinct publication–accession associations. Publication-level modality uses the most frequent modality label across the linked datasets. If labels tie, the first label in the sorted list returned by pandas is selected, so repeated runs make the same choice. The `Review.CollapsedByStudy` sheet is a browsing aid; calculations use Review and the expanded/deduplicated tables.

## Reproduce the archived analysis

Use Python 3.10 or later in an activated virtual environment. From this package's directory:

```bash
cd code
python -m pip install -r requirements.txt
python generate_mtbls_reuse_analysis_outputs.py \
  --review-xlsx ../data/mtbls_reuse_reviewed_records.xlsx \
  --metadata-xlsx ../data/mtbls_reuse_reviewed_records.xlsx \
  --existing-output-dir ../results \
  --output-dir ../recalculated_results
```

The supplied reviewed workbook is the analysis input and includes a saved copy of the dataset metadata. Manual screening decisions are provided as research data; the code does not independently recreate those human decisions. The command writes a new result directory and leaves archived inputs unchanged. The output directory must not already exist.

The two analysis helpers, `repo_counts_from_review_sheet_corrected_study_ids.py` and `mtbls_reuse_plots.py`, are alongside the generator in `code/`. Default paths are resolved relative to this release, independently of the current working directory. The generator and counts helper use `data/mtbls_reuse_reviewed_records.xlsx`; the plotter uses the deduplicated workbook in `results/` and metadata from the reviewed workbook. With no path options, the generator writes to `recalculated_results/` and the standalone plotter writes to `regenerated_plots/`. Command-line options can select other reviewed datasets and output locations.

The analysis writes the same three scientific workbook filenames as `results/`, plus individual plots, a comparison workbook, and local JSON files containing metrics and a record of the inputs used. Compare workbook sheets and cells rather than ZIP-file hashes when checking newly generated Excel files. The released numerical results are available in `results/mtbls_reuse_manuscript_metrics.xlsx`; no pre-existing JSON metric file is needed.

The supplied `data/review_retrieval_provenance.xlsx` records source-search evidence for the archived reviewed rows. It is retained as supporting data; its manuscript-specific generation script is not part of this public workflow.

To run the offline tests from `code/`:

```bash
python -m unittest discover -s tests
```

## Run a new literature search

The `code/config.example.json` template has empty API-key fields and example NCBI tool-name and contact-email values. Copy it to `code/config.json`, supply credentials you are authorised to use and keep that file private. The collection workflow depends on external APIs and source indexing, so a new search will not necessarily reproduce the archived January 2026 corpus. `ELSEVIER_API_KEY` enables ScienceDirect requests, `SPRINGER_API_KEY` enables Springer DOI lookups, and `PMC_API_KEY` supplies an optional NCBI key. Set `NCBI_TOOL_NAME` and `NCBI_TOOL_EMAIL` to identify your tool and contact address in NCBI requests.

Run the search from `code/`, because configuration is loaded from the current working directory:

```bash
python main_mtbls_mw_gnps_reuse.py --help
python main_mtbls_mw_gnps_reuse.py --limit_mtbls 50 --per_id_limit 100 --basename mtbls_reuse
```

`--limit_mtbls` limits accessions searched. Omitting it searches the available public MTBLS accession list. `--per_id_limit` caps hits per accession and source; omitting it preserves provider-specific retrieval limits. The released collectors retrieve one API result page or RSS feed per accession without pagination, so exhaustive retrieval is not guaranteed. The supplied command-line workflow searches MTBLS accessions; MW/GNPS command-line collection options are disabled. The underlying collector functions can be called with other identifiers as described below.

## Look up datasets from other repositories

The source-specific functions in `code/collect_from_*.py` accept a list of dataset identifiers through their `study_ids` argument. They can search the same five literature sources for Metabolomics Workbench accessions (`ST` followed by six digits) and MassIVE/GNPS datasets identified by MassIVE accessions (`MSV` followed by nine digits). The retained functions in `code/collect_study_metadata.py` retrieve accession lists and metadata for these repositories. These capabilities are available by calling the Python functions directly; the supplied command-line workflow runs the MetaboLights search.

The same identifier-based lookup approach can be extended to other datasets or repositories whose identifiers appear in searchable literature. To support the complete workflow for another repository, supply its accession list, add its metadata retrieval and identifier patterns to the review preparation code, and adapt the analysis and plotting steps to its metadata and counting rules. All candidate publications still require manual screening to establish active data reuse.

The supplied reviewed records, results and reproduction commands concern MetaboLights. The manuscript analysis and plotting scripts select MTBLS associations and use MetaboLights metadata, so they require adaptation for analyses of other repositories.

## Prepare a review of new publications

Use the new search workbook's Combined sheet to prepare a blank review workbook, for example from `code/`:

```bash
python build_review_workbook.py \
  --in path/to/new_search.xlsx \
  --in-sheet Combined \
  --out path/to/new_review.xlsx
```

The current collector includes a metadata sheet that the review builder embeds automatically. Screen and classify the new publications manually. Enter a screening decision in `Reviewer.Notes` for every candidate. For confirmed reuse, use `Reuse`, `Benchmark` or `Reanalysis`, and fill the primary category, sub-category and domain fields. For exclusions, use `Reference paper`, `Mention`, `Published preprint`, `Duplicate`, `Wrong Citation`, `No access`, `False Positive` or `Correction/Erratum`.

The `Status` column is a progress marker based only on whether a primary category has been selected; it does not check that screening is complete. Run the analysis command above with `--review-xlsx` and `--metadata-xlsx` pointing to the completed review workbook, and choose a new output directory. Historical annotation replay is not required for new publications.

## Release scope and provenance

The scientific pipeline is supplied with its dependencies and tests. Manuscript editing, historical corpus preparation and annotation replay, retrieval-provenance generation, submission figure assembly and package-building scripts remain local preparation tools. The assembled submission figures are included as outputs; the public pipeline regenerates their individual analysis panels.

The code was prepared from repository commit `54c42a7cb8e8c6607bf2bfe7e955a1a22b5fcbba` and updated for this public release. No private `config.json` is included. Version 1.0.0 of this release is archived on [Zenodo](https://doi.org/10.5281/zenodo.23765512).

## License

The project code and original contributions are available under the [Apache License 2.0](LICENSE), consistent with the source project's existing license declaration. Included third-party publication text and repository metadata retain their original rights and terms; this license does not grant additional rights to that material.

## Citation

Version 1.0.0 of the code, reviewed records, analysis results and figures is archived on [Zenodo](https://doi.org/10.5281/zenodo.23765512). The code repository is [EBI-Metabolights/metabolights-data-reuse](https://github.com/EBI-Metabolights/metabolights-data-reuse). Citation metadata are provided in [CITATION.cff](CITATION.cff).
