"""Expand reviewed publication records into dataset links and remove repeated pairs.

Read the Review sheet. Use manually corrected dataset IDs when the cell is
populated; otherwise use detected IDs. Match publications by DOI when
available, then by a uniquely linked PMCID, PMCID itself or cleaned title.
Different DOIs remain separate, including preprint and journal versions.

Print counts before and after removing repeated publication-dataset pairs.
Optional output workbooks contain the expanded records and one row for each
distinct publication-dataset pair. Reuse decisions are supplied by the
reviewer, not inferred here. The public data are MTBLS-only; older repository
counting helpers are retained for compatibility.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import unicodedata
import pandas as pd

# ---------------------------
# Default reviewed workbook and decision labels
# ---------------------------
EXCEL_PATH = (
    Path(__file__).resolve().parent.parent
    / "data"
    / "mtbls_reuse_reviewed_records.xlsx"
)
REVIEW_SHEET = "Review"
CONFIRMED_NOTES = {"Reuse", "Benchmark", "Reanalysis"}

# ---------------------------
# Publication matching and dataset-link helpers
# ---------------------------
MW_PATTERN = re.compile(r"\bST\d+\b")


def normalized_title(value: object) -> str:
    """Ignore case, repeated spacing and a final full stop when matching records without IDs."""
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).strip().removesuffix(".").casefold().split())


def normalized_doi(value: object) -> str:
    """Remove a DOI link prefix, surrounding spaces and letter-case differences."""
    if pd.isna(value):
        return ""
    doi = str(value).strip().lower()
    return re.sub(r"^https?://(?:dx\.)?doi\.org/", "", doi)


def add_publication_ids(frame: pd.DataFrame) -> pd.DataFrame:
    """Assign a matching label to each publication without merging different DOIs.

    Use DOI first. For a record without a DOI, use a DOI linked to the same
    PMCID only when there is exactly one such DOI in the input. Stop for manual
    review if several DOIs share that PMCID. Otherwise use the PMCID itself or,
    when both identifiers are absent, the cleaned title. PMCID means the article
    identifier used by PubMed Central.
    """
    out = frame.copy()
    dois = out["DOI"].map(normalized_doi) if "DOI" in out else pd.Series("", index=out.index)
    pmcids = (
        out["PMCID"].fillna("").astype(str).str.strip().str.upper()
        if "PMCID" in out else pd.Series("", index=out.index)
    )
    doi_by_pmcid: dict[str, set[str]] = {}
    for pmcid, doi in zip(pmcids, dois):
        if pmcid and doi:
            doi_by_pmcid.setdefault(pmcid, set()).add(doi)

    identities = []
    for title, doi, pmcid in zip(out["Title"], dois, pmcids):
        if doi:
            identities.append(f"doi:{doi}")
        elif pmcid:
            linked = doi_by_pmcid.get(pmcid, set())
            if len(linked) > 1:
                raise ValueError(f"Record without a DOI has ambiguous PMCID {pmcid}: {sorted(linked)}")
            identities.append(f"doi:{next(iter(linked))}" if linked else f"pmcid:{pmcid}")
        else:
            identities.append(f"title:{normalized_title(title)}")
    out["Publication ID"] = identities
    return out

def coalesce_corrected_ids(df: pd.DataFrame) -> pd.Series:
    """Use corrected IDs unless the corrected cell is missing, then use detected IDs.

    An empty Excel cell is read as missing by pandas. The function expects
    that representation; it does not replace an explicit empty text string.
    """
    source_col = "StudyIDs (detected)" if "StudyIDs (detected)" in df.columns else "StudyIDs"
    return df["StudyIDs (corrected)"].fillna(df[source_col])

def repo_flags_from_string(s: pd.Series) -> pd.DataFrame:
    """Mark which repository prefixes appear in each record's dataset-ID text."""
    return pd.DataFrame({
        "has_MTBLS": s.str.contains("MTBLS", na=False),
        "has_MW": s.str.contains(MW_PATTERN, na=False),
        "has_GNPS": s.str.contains("MSV", na=False),
    })

def expand_studyids(df: pd.DataFrame, studyids_col: str = "StudyIDs_use") -> pd.DataFrame:
    """Make one row for each comma-separated dataset accession in a reviewed record.

    A paper listing several datasets therefore contributes several rows.
    Remove empty entries and the text nan produced from missing cells.
    """
    out = df.copy()
    out["StudyIDs_split"] = out[studyids_col].astype(str).str.split(",")
    out = out.explode("StudyIDs_split")
    out["StudyIDs_split"] = out["StudyIDs_split"].str.strip()

    # Drop blank entries and missing cells converted to the text nan.
    out = out[out["StudyIDs_split"].notna()]
    out = out[out["StudyIDs_split"] != ""]
    out = out[out["StudyIDs_split"].str.lower() != "nan"]
    return out


def deduplicate_publication_study_prefer_confirmed(expanded: pd.DataFrame) -> pd.DataFrame:
    """Keep one row for each matched publication and dataset accession.

    Prefer a Reuse, Benchmark or Reanalysis decision over other decisions for
    the same pair. If decisions have the same priority, retain the first input
    row. Records with different DOIs remain separate; manual notes determine
    whether each version is included in the confirmed analysis.
    """
    if "Publication ID" not in expanded:
        expanded = add_publication_ids(expanded)
    key = ["Publication ID", "StudyIDs_split"]
    notes_col = next(
        (
            column
            for column in ("Reviewer.Notes (e.g., how the data was used)", "Reviewer.Notes")
            if column in expanded.columns
        ),
        None,
    )
    if notes_col is None:
        return expanded.drop_duplicates(subset=key).copy()

    ranked = expanded.copy()
    ranked["_source_order"] = range(len(ranked))
    ranked["_is_confirmed"] = ranked[notes_col].isin(CONFIRMED_NOTES)
    ranked = ranked.sort_values(
        ["Publication ID", "StudyIDs_split", "_is_confirmed", "_source_order"],
        ascending=[True, True, False, True],
        kind="stable",
    )
    ranked = ranked.drop_duplicates(subset=key, keep="first")
    return ranked.sort_values("_source_order", kind="stable").drop(
        columns=["_source_order", "_is_confirmed"]
    )

def parse_args() -> argparse.Namespace:
    """Read the reviewed-workbook path and optional expanded or deduplicated output paths."""
    parser = argparse.ArgumentParser(
        description="Expand reviewed records into publication-dataset links and remove repeated pairs."
    )
    parser.add_argument("--xlsx", type=Path, default=Path(EXCEL_PATH))
    parser.add_argument("--review-sheet", default=REVIEW_SHEET)
    parser.add_argument(
        "--write-expanded",
        type=Path,
        help="Output workbook with one row per reviewed record and dataset accession.",
    )
    parser.add_argument(
        "--write-deduplicated",
        type=Path,
        help="Output workbook with one row per matched publication and dataset pair.",
    )
    return parser.parse_args()


def main() -> None:
    """Read Review, print publication and dataset counts, and write requested pair tables."""
    args = parse_args()
    df = pd.read_excel(args.xlsx, sheet_name=args.review_sheet)

    # Use manual accession corrections when present, otherwise the detected accessions.
    df_ids = add_publication_ids(df)
    df_ids["StudyIDs_use"] = coalesce_corrected_ids(df_ids)

# ---------------------------
# 1) Count matched publications, using Publication ID rather than title alone.
# ---------------------------
    flags_row = repo_flags_from_string(df_ids["StudyIDs_use"])
    df_ids = pd.concat([df_ids, flags_row], axis=1)

    pub = (
    df_ids.groupby("Publication ID")
    .agg(
        MTBLS_used=("has_MTBLS", "any"),
        MW_used=("has_MW", "any"),
        GNPS_used=("has_GNPS", "any"),
    )
    .reset_index()
    )

    pub_counts = {
    "MTBLS_unique_papers": int(pub[pub["MTBLS_used"]].shape[0]),
    "MW_unique_papers": int(pub[pub["MW_used"]].shape[0]),
    "GNPS_unique_papers": int(pub[pub["GNPS_used"]].shape[0]),
    "Total_unique_papers_any_repo": int(pub.shape[0]),
    }

    print("\nPublication counts (each matched publication counted once)")
    for k, v in pub_counts.items():
        print(f"- {k}: {v}")

# ---------------------------
# 2) Count publications mentioning each combination of repositories.
# ---------------------------
    A_only = int(pub[(pub["MTBLS_used"]) & (~pub["MW_used"]) & (~pub["GNPS_used"])].shape[0])
    B_only = int(pub[(~pub["MTBLS_used"]) & (pub["MW_used"]) & (~pub["GNPS_used"])].shape[0])
    C_only = int(pub[(~pub["MTBLS_used"]) & (~pub["MW_used"]) & (pub["GNPS_used"])].shape[0])
    AB = int(pub[(pub["MTBLS_used"]) & (pub["MW_used"]) & (~pub["GNPS_used"])].shape[0])
    AC = int(pub[(pub["MTBLS_used"]) & (~pub["MW_used"]) & (pub["GNPS_used"])].shape[0])
    BC = int(pub[(~pub["MTBLS_used"]) & (pub["MW_used"]) & (pub["GNPS_used"])].shape[0])
    ABC = int(pub[(pub["MTBLS_used"]) & (pub["MW_used"]) & (pub["GNPS_used"])].shape[0])

    print("\nPublications mentioning each combination of repositories")
    print(f"- MTBLS only: {A_only}")
    print(f"- MW only: {B_only}")
    print(f"- GNPS only: {C_only}")
    print(f"- MTBLS+MW: {AB}")
    print(f"- MTBLS+GNPS: {AC}")
    print(f"- MW+GNPS: {BC}")
    print(f"- MTBLS+MW+GNPS: {ABC}")

# ---------------------------
# 3) Count links after splitting each reviewed record into its dataset accessions.
# ---------------------------
    expanded = expand_studyids(df_ids, studyids_col="StudyIDs_use")
    flags_ds = repo_flags_from_string(expanded["StudyIDs_split"])
    # A publication can mention several repositories. Recalculate these flags
    # for each individual accession after splitting, so links are counted correctly.
    expanded = expanded.drop(columns=flags_ds.columns, errors="ignore")
    expanded = pd.concat([expanded, flags_ds], axis=1)

    rows_per_repo = {
    "MTBLS_rows_expanded": int(expanded[expanded["has_MTBLS"]].shape[0]),
    "MW_rows_expanded": int(expanded[expanded["has_MW"]].shape[0]),
    "GNPS_rows_expanded": int(expanded[expanded["has_GNPS"]].shape[0]),
    "Total_rows_expanded": int(expanded.shape[0]),
    }

    print("\nDataset links before removing repeats (one reviewed record and accession per row)")
    for k, v in rows_per_repo.items():
        print(f"- {k}: {v}")

# ---------------------------
# 4) Count distinct dataset accessions in each repository.
# ---------------------------
    unique_studies = {
    "Total_unique_studies": int(expanded["StudyIDs_split"].nunique()),
    "MTBLS_unique_studies": int(expanded[expanded["has_MTBLS"]]["StudyIDs_split"].nunique()),
    "MW_unique_studies": int(expanded[expanded["has_MW"]]["StudyIDs_split"].nunique()),
    "GNPS_unique_studies": int(expanded[expanded["has_GNPS"]]["StudyIDs_split"].nunique()),
    }

    print("\nUnique studies (unique StudyIDs)")
    for k, v in unique_studies.items():
        print(f"- {k}: {v}")

# ---------------------------
# 5) Keep one link for each matched publication and dataset accession.
# ---------------------------
    expanded_dedup = deduplicate_publication_study_prefer_confirmed(expanded)

    rows_per_repo_dedup = {
    "MTBLS_rows_dedup": int(expanded_dedup[expanded_dedup["has_MTBLS"]].shape[0]),
    "MW_rows_dedup": int(expanded_dedup[expanded_dedup["has_MW"]].shape[0]),
    "GNPS_rows_dedup": int(expanded_dedup[expanded_dedup["has_GNPS"]].shape[0]),
    "Total_rows_dedup": int(expanded_dedup.shape[0]),
    "Duplicates_removed": int(expanded.shape[0] - expanded_dedup.shape[0]),
    }

    print("\nDistinct publication-dataset pairs (confirmed screening decisions take priority)")
    for k, v in rows_per_repo_dedup.items():
        print(f"- {k}: {v}")

    if args.write_expanded:
        args.write_expanded.parent.mkdir(parents=True, exist_ok=True)
        expanded.to_excel(args.write_expanded, index=False)
    if args.write_deduplicated:
        args.write_deduplicated.parent.mkdir(parents=True, exist_ok=True)
        expanded_dedup.to_excel(args.write_deduplicated, index=False)


if __name__ == "__main__":
    main()
