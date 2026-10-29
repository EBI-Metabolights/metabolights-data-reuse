"""Calculate reuse results and plots from a manually reviewed MTBLS workbook.

Call the counts helper to create publication-dataset tables, then combine
confirmed records with dataset metadata. Count publications once for
publication summaries and count distinct publication-dataset pairs for
dataset summaries. Write the result workbooks, a comparison with the
supplied results, file checksums and individual plot panels.

Screening decisions and category labels must already be recorded manually.
Defaults use the data/ and results/ folders supplied with this release.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import unicodedata

import pandas as pd


CONFIRMED_NOTES = {"Reuse", "Benchmark", "Reanalysis"}
SCREENING_PRIORITY = [
    "Reference paper", "Mention", "Published preprint", "Duplicate",
    "Wrong Citation", "No access", "False Positive", "Correction/Erratum",
]
JOURNAL_HARMONIZATION = {
    # Combine only the source-name variants explicitly listed below.
    # Keep original source text in the review data. The records have no ISSNs,
    # so this list is based on inspected equivalent names rather than an
    # external journal-identifier lookup.
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


def sha256_file(path: Path) -> str:
    """Calculate a file checksum for the record of which inputs and scripts were used."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_title(value: object) -> str:
    """Remove surrounding whitespace and one final full stop from a title."""
    title = str(value).strip()
    return title[:-1] if title.endswith(".") else title


def comparison_title(value: object) -> str:
    """Make equivalent text characters and repeated spacing comparable in old title lists."""
    title = unicodedata.normalize("NFKC", str(value)).strip()
    return " ".join(title.split()).removesuffix(".")


def split_subcategories(value: object) -> list[str]:
    """Split nonempty sub-category labels at commas or semicolons; label missing text as (none)."""
    if pd.isna(value) or not str(value).strip():
        return ["(none)"]
    return [item.strip() for item in re.split(r"[;,]+", str(value)) if item.strip()]


def validate_publication_subcategories(expanded: pd.DataFrame) -> None:
    """Stop when one confirmed publication has conflicting manual sub-categories.

    Check all confirmed input rows before repeated pairs are removed, so a
    conflicting label cannot disappear during deduplication. Excluded rows
    do not determine a confirmed publication's classification.
    """
    confirmed = expanded.loc[expanded["Reviewer.Notes"].isin(CONFIRMED_NOTES)]
    for publication_id, group in confirmed.groupby("Publication ID", sort=False):
        labels = sorted({
            label
            for value in group["Subcategories (free text)"].dropna()
            for label in split_subcategories(value)
        })
        if len(labels) > 1:
            raise ValueError(
                f"Conflicting confirmed subcategories for {publication_id}: {labels}. "
                "Resolve the publication's manual assignment in the Review sheet."
            )


def mode_first(values: pd.Series, default: str) -> str:
    """Return the most frequent nonmissing label, or default when none exists.

    If labels tie, take the first value returned by pandas.Series.mode,
    which sorts these text labels. This makes repeated runs choose the same label.
    """
    modes = values.mode(dropna=True)
    return str(modes.iloc[0]) if not modes.empty else default


def infer_modality(text: object, accession: object) -> str:
    """Assign analytical modality from the dataset's technology and descriptor text.

    Recognise LC-MS, GC-MS, NMR and other MS terms; combine recognised methods
    with ampersands. Return Unknown when no supported term is found. MTBLS28
    is explicitly assigned LC-MS from its study description because the
    metadata fields used here do not otherwise identify that method.
    """
    if str(accession).upper() == "MTBLS28":
        return "LC-MS"

    value = str(text or "").lower().replace("-", " ").replace("/", " ").replace("_", " ")
    has_nmr = "nuclear magnetic resonance" in value or " nmr" in value or "nmr " in value
    has_lc = "liquid chromatography" in value or "lc ms" in value or "lcms" in value
    has_gc = "gas chromatography" in value or "gc ms" in value or "gcms" in value
    ms_words = [
        "mass spectrometry",
        " orbitrap",
        " exactive",
        " qtof",
        " tof",
        "triple quadrupole",
        "quadrupole",
        "ft icr",
        "fticr",
    ]
    has_ms = any(word in value for word in ms_words) or " ms " in f" {value} "

    labels: list[str] = []
    if has_lc:
        labels.append("LC-MS")
    if has_gc:
        labels.append("GC-MS")
    if has_nmr:
        labels.append("NMR")
    if not labels and has_ms:
        labels.append("MS (other/unspecified)")
    if not labels:
        return "Unknown"

    order = {"LC-MS": 1, "GC-MS": 2, "NMR": 3, "MS (other/unspecified)": 4}
    return "&".join(sorted(dict.fromkeys(labels), key=lambda item: order[item]))


def load_metadata(path: Path) -> pd.DataFrame:
    """Read the saved MTBLS_metadata sheet and derive modality and release year.

    Use technology, platform and study-descriptor text for modality, and
    MTBLS.ReleaseDate for release year. Return one copy of each distinct
    accession/modality/year combination for joining to reviewed dataset links.
    """
    metadata = pd.read_excel(path, sheet_name="MTBLS_metadata")
    for column in ["MTBLS.Technologies", "MTBLS.Platforms", "MTBLS.Descriptors"]:
        if column not in metadata:
            metadata[column] = ""
    metadata["ReleaseYear"] = pd.to_datetime(
        metadata.get("MTBLS.ReleaseDate"), errors="coerce"
    ).dt.year
    text = (
        metadata[["MTBLS.Technologies", "MTBLS.Platforms", "MTBLS.Descriptors"]]
        .fillna("")
        .astype(str)
        .agg(" ".join, axis=1)
    )
    metadata["Modality"] = [
        infer_modality(row_text, accession)
        for row_text, accession in zip(text, metadata["MTBLS.Accession"])
    ]
    return metadata[["MTBLS.Accession", "Modality", "ReleaseYear"]].drop_duplicates()


def subcategory_crosstab(
    rows: pd.DataFrame,
    group_column: str,
    subcategory_column: str,
    unique_publications: bool,
    threshold: int = 5,
) -> pd.DataFrame:
    """Count sub-categories against the chosen category or modality labels.

    For publication counts, remove repeated publication/group/sub-category
    combinations. Otherwise count each supplied dataset association. Retain
    sub-categories whose total count reaches threshold across the whole table.
    """
    entries: list[tuple[object, ...]] = []
    for _, row in rows.iterrows():
        for subcategory in split_subcategories(row[subcategory_column]):
            if unique_publications:
                entries.append((row["Publication ID"], row[group_column], subcategory))
            else:
                entries.append((row[group_column], subcategory))

    names = ["Publication ID", group_column, "Subcategory"] if unique_publications else [group_column, "Subcategory"]
    long = pd.DataFrame(entries, columns=names)
    if unique_publications:
        long = long.drop_duplicates()
    table = pd.crosstab(long[group_column], long["Subcategory"])
    table = table.loc[:, table.sum(axis=0).ge(threshold)]
    return table.loc[:, table.sum(axis=0).sort_values(ascending=False).index]


def write_workbook(path: Path, sheets: dict[str, pd.DataFrame]) -> None:
    """Write the supplied tables to Excel with a frozen header row and readable column widths."""
    with pd.ExcelWriter(path, engine="xlsxwriter") as writer:
        for name, frame in sheets.items():
            frame.to_excel(writer, sheet_name=name[:31], index=False)
            worksheet = writer.sheets[name[:31]]
            worksheet.freeze_panes(1, 0)
            for index, column in enumerate(frame.columns):
                values = frame[column].fillna("") if not frame.empty else pd.Series(dtype=object)
                value_width = int(values.map(lambda value: len(str(value))).max()) + 2 if not values.empty else 0
                width = min(60, max(len(str(column)) + 2, value_width))
                worksheet.set_column(index, index, width)


def build_result_tables(
    deduplicated: pd.DataFrame, metadata: pd.DataFrame, expanded: pd.DataFrame
) -> dict[str, pd.DataFrame]:
    """Build screening, publication, dataset and yearly summaries from reviewed records.

    Use the manual Reuse, Benchmark and Reanalysis labels for confirmed rows.
    Create one final screening outcome per publication using the stated
    priority order. Confirmed rows need a publication year for these results.
    Join dataset metadata and choose each publication's most frequent modality
    label when it links to several datasets. Title-group counts are retained
    only for comparison with earlier counts, not as the publication denominator.
    """
    validate_publication_subcategories(expanded)
    notes_column = "Reviewer.Notes"
    primary_column = "Primary (pick)"
    domain_column = "Domain (pick)"
    subcategory_column = "Subcategories (free text)"

    deduplicated = deduplicated.copy()
    deduplicated["Title_norm"] = deduplicated["Title"].map(normalize_title)
    mtbls_rows = deduplicated[
        deduplicated["StudyIDs_split"].astype(str).str.contains("MTBLS", na=False)
    ].copy()
    # Each matched publication and accession should already occur once.
    initial_pairs = mtbls_rows.drop_duplicates(["Publication ID", "StudyIDs_split"])
    # Keep the earlier title-group total as a comparison value only.
    # Screening outcomes and final publication totals use Publication ID instead.
    screening_title_groups = (
        expanded.loc[
            expanded["StudyIDs_split"].astype(str).str.contains("MTBLS", na=False),
            "Title",
        ]
        .map(normalize_title)
        .nunique()
    )
    screening_rows = expanded.loc[
        expanded["StudyIDs_split"].astype(str).str.contains("MTBLS", na=False)
    ].copy()
    screening_outcomes = []
    for publication_id, group in screening_rows.groupby("Publication ID", sort=False):
        decisions = set(group[notes_column].dropna().astype(str))
        if decisions & CONFIRMED_NOTES:
            outcome = "Confirmed reuse"
        else:
            outcome = next((decision for decision in SCREENING_PRIORITY if decision in decisions), None)
        if outcome is None:
            raise ValueError(f"No screening outcome for {publication_id}: {sorted(decisions)}")
        screening_outcomes.append({
            "Publication ID": publication_id,
            "Titles": "; ".join(sorted(group["Title"].dropna().astype(str).unique())),
            "Reviewer decisions": "; ".join(sorted(decisions)),
            "Final outcome": outcome,
        })
    screening_outcomes = pd.DataFrame(screening_outcomes)
    screening_counts = (
        screening_outcomes["Final outcome"].value_counts()
        .rename_axis("Final outcome").reset_index(name="Publication identities")
    )
    outcome_counts = dict(zip(screening_counts["Final outcome"], screening_counts["Publication identities"]))

    confirmed = mtbls_rows[mtbls_rows[notes_column].isin(CONFIRMED_NOTES)].copy()
    confirmed = confirmed.dropna(subset=["Year"]).copy()
    confirmed["Year"] = confirmed["Year"].astype(int)
    confirmed = confirmed.merge(
        metadata,
        how="left",
        left_on="StudyIDs_split",
        right_on="MTBLS.Accession",
    )
    confirmed["Modality"] = confirmed["Modality"].fillna("MS (other/unspecified)")
    confirmed["Primary"] = confirmed[primary_column]
    confirmed["Domain"] = confirmed[domain_column]
    confirmed["Publication_source_harmonized"] = confirmed["Journal"].replace(JOURNAL_HARMONIZATION)

    publications = (
        confirmed.groupby("Publication ID", as_index=False, sort=False)
        .agg(
            Title=("Title", "first"),
            Year=("Year", "first"),
            Primary=("Primary", "first"),
            Domain=("Domain", "first"),
            Journal=("Journal", "first"),
            Publication_source_harmonized=("Publication_source_harmonized", "first"),
            Modality=("Modality", lambda values: mode_first(values, "MS (other/unspecified)")),
        )
    )
    if len(screening_outcomes) != mtbls_rows["Publication ID"].nunique():
        raise AssertionError("Screening outcomes do not cover every publication identity")
    if int((screening_outcomes["Final outcome"] == "Confirmed reuse").sum()) != len(publications):
        raise AssertionError("Screening and confirmed-publication counts disagree")

    study_counts = (
        confirmed.groupby("StudyIDs_split").size().sort_values(ascending=False).rename("PublicationStudyMatches")
    )
    study_count_table = study_counts.rename_axis("StudyIDs_split").reset_index()
    study_bins = pd.DataFrame(
        {
            "Bin": ["1 reuse", "2-4 reuses", "5+ reuses"],
            "UniqueStudies": [
                int((study_counts == 1).sum()),
                int(study_counts.between(2, 4).sum()),
                int((study_counts >= 5).sum()),
            ],
        }
    )

    numbers = pd.DataFrame(
        [
            ("Initial_MTBLS_publication_study_matches", len(initial_pairs)),
            ("Initial_unique_publications_with_MTBLS_mentions", mtbls_rows["Publication ID"].nunique()),
            ("Initial_unique_title_groups", screening_title_groups),
            ("Initial_unique_MTBLS_studies_mentioned", mtbls_rows["StudyIDs_split"].nunique()),
            ("Confirmed_publication_study_matches", len(confirmed)),
            ("Confirmed_unique_publications", publications["Publication ID"].nunique()),
            ("Confirmed_unique_MTBLS_studies", confirmed["StudyIDs_split"].nunique()),
            ("Confirmed_studies_reused_once", int((study_counts == 1).sum())),
            ("Confirmed_studies_reused_2_to_4_times", int(study_counts.between(2, 4).sum())),
            ("Confirmed_studies_reused_5_or_more_times", int((study_counts >= 5).sum())),
            ("Distinct_publication_sources_confirmed_reuse", publications["Publication_source_harmonized"].nunique()),
            ("Screening_excluded_publications", int((screening_outcomes["Final outcome"] != "Confirmed reuse").sum())),
            ("Screening_submission_references", outcome_counts.get("Reference paper", 0)),
            ("Screening_mentions", outcome_counts.get("Mention", 0)),
            ("Screening_published_preprints", outcome_counts.get("Published preprint", 0)),
            ("Screening_duplicates", outcome_counts.get("Duplicate", 0)),
            ("Screening_wrong_citations", outcome_counts.get("Wrong Citation", 0)),
            ("Screening_no_access", outcome_counts.get("No access", 0)),
            ("Screening_false_positives", outcome_counts.get("False Positive", 0)),
            ("Screening_corrections_or_errata", outcome_counts.get("Correction/Erratum", 0)),
        ],
        columns=["Metric", "Value"],
    )

    yearly_primary = pd.crosstab(publications["Year"], publications["Primary"]).reset_index()
    yearly_domain = pd.crosstab(publications["Year"], publications["Domain"]).reset_index()
    yearly_modality = pd.crosstab(publications["Year"], publications["Modality"]).reset_index()

    primary_subcategory_source = (
        confirmed.groupby("Publication ID", as_index=False, sort=False)
        .agg(
            Primary=("Primary", "first"),
            Subcategories=(subcategory_column, lambda values: ";".join(map(str, values.dropna().unique()))),
        )
    )
    modality_subcategory_source = (
        confirmed.groupby("Publication ID", as_index=False, sort=False)
        .agg(
            Modality=("Modality", lambda values: mode_first(values, "MS (other/unspecified)")),
            Subcategories=(subcategory_column, lambda values: ";".join(map(str, values.dropna().unique()))),
        )
    )

    tables = {
        "manuscript_numbers": numbers,
        "primary_counts": publications["Primary"].value_counts().rename_axis("Primary").reset_index(name="UniquePublications"),
        "domain_counts": publications["Domain"].value_counts().rename_axis("Domain").reset_index(name="UniquePublications"),
        "modality_counts": publications["Modality"].value_counts().rename_axis("Modality").reset_index(name="UniquePublications"),
        "publication_source_counts": publications["Publication_source_harmonized"].value_counts().rename_axis("Publication source").reset_index(name="UniquePublications"),
        "study_reuse_counts": study_count_table,
        "study_reuse_bins": study_bins,
        "top20_studies": study_count_table.head(20),
        "yearly_primary": yearly_primary,
        "yearly_domain": yearly_domain,
        "yearly_modality": yearly_modality,
        "heat_primary_modality_unique": pd.crosstab(publications["Primary"], publications["Modality"]).reset_index(),
        "heat_primary_modality_all": pd.crosstab(confirmed["Primary"], confirmed["Modality"]).reset_index(),
        "heat_primary_subcategory_unique": subcategory_crosstab(
            primary_subcategory_source, "Primary", "Subcategories", unique_publications=True
        ).reset_index(),
        "heat_modality_subcategory_unique": subcategory_crosstab(
            modality_subcategory_source, "Modality", "Subcategories", unique_publications=True
        ).reset_index(),
        "heat_modality_subcategory_all": subcategory_crosstab(
            confirmed, "Modality", subcategory_column, unique_publications=False
        ).reset_index(),
        "publication_table": publications,
        "screening_outcomes": screening_outcomes,
        "screening_counts": screening_counts,
        "df_confirmed_mtbls_matches": confirmed,
    }
    return tables


def write_comparison(output_dir: Path, tables: dict[str, pd.DataFrame], existing_dir: Path) -> pd.DataFrame:
    """Compare numerical results and publication-dataset keys with an earlier workbook.

    Use the current metrics filename, or the supported older filename if it is
    absent. The detailed key check compares cleaned title and accession for
    compatibility with older records; it is not a full DOI-based equivalence
    check. Write counts, differences and notes to a separate workbook.
    """
    existing_path = existing_dir / "mtbls_reuse_manuscript_metrics.xlsx"
    if not existing_path.is_file():
        # Accept the older metrics filename when the current filename is absent.
        # Only the current metrics filename is needed in this public release.
        existing_path = existing_dir / "mtbls_reuse_manuscript_numbers.xlsx"
    existing = pd.read_excel(existing_path, sheet_name="manuscript_numbers")
    # Older result tables called this source count a journal count.
    # Treat the two names as the same measure when comparing result files.
    existing["Metric"] = existing["Metric"].replace({
        "Distinct_journals_confirmed_reuse": "Distinct_publication_sources_confirmed_reuse",
    })
    current = tables["manuscript_numbers"]
    comparison = existing.merge(current, on="Metric", how="outer", suffixes=("_existing", "_recalculated"))
    comparison["Delta"] = comparison["Value_recalculated"] - comparison["Value_existing"]

    old_matches = pd.read_excel(
        existing_path, sheet_name="df_confirmed_mtbls_matches"
    )
    key_columns = ["_comparison_title", "StudyIDs_split"]
    new_matches = tables["df_confirmed_mtbls_matches"].copy()
    old_matches = old_matches.copy()
    old_matches["_comparison_title"] = old_matches["Title"].map(comparison_title)
    new_matches["_comparison_title"] = new_matches["Title"].map(comparison_title)
    old_keys = old_matches[key_columns].drop_duplicates()
    new_keys = new_matches[key_columns].drop_duplicates()
    new_only = new_matches.merge(old_keys, on=key_columns, how="left", indicator=True)
    new_only = new_only.loc[new_only["_merge"] == "left_only"].drop(columns="_merge")
    old_only = old_matches.merge(new_keys, on=key_columns, how="left", indicator=True)
    old_only = old_only.loc[old_only["_merge"] == "left_only"].drop(columns="_merge")
    new_only = new_only.drop_duplicates(key_columns).drop(columns="_comparison_title")
    old_only = old_only.drop_duplicates(key_columns).drop(columns="_comparison_title")

    write_workbook(
        output_dir / "comparison_to_existing_outputs.xlsx",
        {
            "manuscript_numbers": comparison,
            "new_title_study_keys": new_only,
            "removed_title_study_keys": old_only,
            "comparison_notes": pd.DataFrame({
                "Note": [
                    "The detailed record comparison uses cleaned title and MTBLS accession for compatibility with older files. It does not fully check whether the same publications were matched by DOI or PMCID.",
                    "Use the metrics table and the supplied publication-dataset workbooks for the final publication and association counts.",
                ]
            }),
        },
    )
    return comparison


def parse_args() -> argparse.Namespace:
    """Read input and output paths, with defaults relative to the public release directory."""
    package_dir = Path(__file__).resolve().parent.parent
    review = package_dir / "data" / "mtbls_reuse_reviewed_records.xlsx"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review-xlsx", type=Path, default=review)
    parser.add_argument("--metadata-xlsx", type=Path, default=review)
    parser.add_argument("--existing-output-dir", type=Path, default=package_dir / "results")
    parser.add_argument("--output-dir", type=Path, default=package_dir / "recalculated_results")
    return parser.parse_args()


def main() -> None:
    """Generate pair tables, result summaries, comparisons, plots and input-file checksums."""
    args = parse_args()
    if args.output_dir.exists():
        raise FileExistsError(f"Refusing to overwrite existing output directory: {args.output_dir}")
    args.output_dir.mkdir(parents=True)

    helper = Path(__file__).resolve().parent / "repo_counts_from_review_sheet_corrected_study_ids.py"
    expanded_path = args.output_dir / "mtbls_review_expanded_publication_study_records.xlsx"
    deduplicated_path = args.output_dir / "mtbls_review_deduplicated_publication_study_records.xlsx"
    subprocess.run(
        [
            sys.executable,
            str(helper),
            "--xlsx",
            str(args.review_xlsx),
            "--write-expanded",
            str(expanded_path),
            "--write-deduplicated",
            str(deduplicated_path),
        ],
        check=True,
    )

    expanded = pd.read_excel(expanded_path)
    deduplicated = pd.read_excel(deduplicated_path)
    metadata = load_metadata(args.metadata_xlsx)
    tables = build_result_tables(deduplicated, metadata, expanded)
    write_workbook(args.output_dir / "mtbls_reuse_manuscript_metrics.xlsx", tables)
    tables["manuscript_numbers"].set_index("Metric")["Value"].to_json(
        args.output_dir / "mtbls_reuse_manuscript_metrics.json", indent=2
    )
    comparison = write_comparison(args.output_dir, tables, args.existing_output_dir)

    plotter = Path(__file__).resolve().parent / "mtbls_reuse_plots.py"
    subprocess.run(
        [
            sys.executable,
            str(plotter),
            "--review-xlsx",
            str(deduplicated_path),
            "--metadata-xlsx",
            str(args.metadata_xlsx),
            "--outdir",
            str(args.output_dir),
        ],
        check=True,
    )

    provenance = {
        "review_xlsx": str(args.review_xlsx),
        "review_xlsx_sha256": sha256_file(args.review_xlsx),
        "metadata_xlsx": str(args.metadata_xlsx),
        "metadata_xlsx_sha256": sha256_file(args.metadata_xlsx),
        "generator": str(Path(__file__).resolve()),
        "generator_sha256": sha256_file(Path(__file__).resolve()),
        "deduplication_helper": str(helper),
        "deduplication_helper_sha256": sha256_file(helper),
        "plotter": str(plotter),
        "plotter_sha256": sha256_file(plotter),
        "deduplication_rule": "Keep one record per publication and dataset accession. Prefer a Reuse, Benchmark or Reanalysis decision; otherwise retain input order. Match publications by DOI, by a DOI uniquely linked to the PMCID, by PMCID itself, or by cleaned title when identifiers are absent. Keep different DOIs separate, including preprint and journal versions.",
        "confirmed_filter": sorted(CONFIRMED_NOTES),
        "comparison_metrics_changed": comparison.loc[comparison["Delta"] != 0].to_dict(orient="records"),
    }
    (args.output_dir / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
    print(f"Wrote recalculated outputs to: {args.output_dir}")


if __name__ == "__main__":
    main()
