"""Collect candidate publications mentioning public MetaboLights accessions.

Retrieve MTBLS accessions and metadata, search the five literature sources,
remove matches to repository-recorded reference DOIs and combine repeated
search records. Write per-source and Combined sheets to an Excel workbook.
Screening and reuse classification are performed manually afterwards.
The filename retains MW/GNPS wording, but this entry point searches MTBLS only.
"""

import argparse
import datetime
from pathlib import Path
import unicodedata
import pandas as pd
from collect_from_elsevier_sciencedirect import collect_sciencedirect_api
from collect_from_europepmc import collect_europe_pmc
from collect_from_pmc import collect_pmc_esearch
from collect_from_springer import collect_springer_rss, enrich_springer_rows_with_bibliography
from collect_from_wiley import collect_wiley_search_rss
from collect_study_metadata import fetch_mtbls_ids, get_study_metadata


def _normalise_title_for_dedup(value: object) -> str:
    """Make title case, spacing and a final full stop comparable when DOI is missing."""
    title = unicodedata.normalize("NFKC", str(value or ""))
    return " ".join(title.split()).casefold().removesuffix(".")


def dedupe_combined(df: pd.DataFrame) -> pd.DataFrame:
    """Keep one candidate for each matching publication and dataset within each path.

    For records with a DOI, compare DOI and dataset accession. Handle records
    without a DOI separately, comparing title after simple text cleanup,
    publication year and accession. Prefer PMC, Europe PMC, ScienceDirect,
    Springer, then Wiley when equivalent candidates come from several sources.
    Matching here combines search records; manual review still decides reuse.
    """
    if df is None or df.empty:
        return df
    if "Match.DOI" not in df.columns or "Study ID" not in df.columns:
        raise ValueError("Deduplication requires 'Match.DOI' and 'Study ID' columns")

    source_rank = {"PMC": 0, "EuropePMC": 1, "ScienceDirectAPI": 2, "Springer": 3, "Wiley": 4}
    working = df.copy()
    working["__rank"] = working.get("Source", pd.Series(index=working.index, dtype=str)).map(source_rank).fillna(9)
    working["__doi_key"] = working["Match.DOI"].fillna("").astype(str).str.strip().str.casefold()

    with_doi = working.loc[working["__doi_key"].ne("")].sort_values(["__doi_key", "__rank"])
    with_doi = with_doi.drop_duplicates(subset=["__doi_key", "Study ID"], keep="first")

    without_doi = working.loc[working["__doi_key"].eq("")].copy()
    if without_doi.empty:
        return with_doi.drop(columns=["__rank", "__doi_key"], errors="ignore").reset_index(drop=True)
    if "Match.Title" not in without_doi.columns or "Match.Year" not in without_doi.columns:
        raise ValueError("Records without a DOI require 'Match.Title' and 'Match.Year' columns")

    without_doi["__title_key"] = without_doi["Match.Title"].map(_normalise_title_for_dedup)
    without_doi["__year_key"] = without_doi["Match.Year"].fillna("").astype(str).str.strip()
    without_doi = without_doi.sort_values(["__title_key", "__year_key", "__rank"])
    without_doi = without_doi.drop_duplicates(
        subset=["__title_key", "__year_key", "Study ID"], keep="first"
    )

    return pd.concat([with_doi, without_doi], ignore_index=True).drop(
        columns=["__rank", "__doi_key", "__title_key", "__year_key"], errors="ignore"
    )

def exclude_primary_publications(df: pd.DataFrame) -> pd.DataFrame:
    """Remove hits whose DOI text matches a repository-recorded reference DOI.

    Compare Match.DOI with the semicolon-separated publication DOI fields
    returned by the repositories. This is an initial filter; manual screening
    also checks original publications that repository metadata did not identify.
    Keep candidates without a DOI for manual review, and ignore missing
    repository reference values instead of converting them to text.
    """
    if df is None or df.empty:
        return df

    def _is_primary(row) -> bool:
        """Compare this hit with the reference DOI values stored in its metadata fields."""
        raw_doi = row.get('Match.DOI', '')
        if pd.isna(raw_doi):
            return False
        doi = str(raw_doi or '').strip().lower()
        if not doi:
            return False
        candidates = []
        for key in ('MTBLS.PublicationDOIs', 'MW.PublicationDOIs', 'GNPS.PublicationDOIs'):
            raw_reference = row.get(key, '')
            if pd.isna(raw_reference):
                continue
            val = str(raw_reference or '').lower().split(';')
            candidates.extend([v.strip() for v in val if v.strip()])
        return doi in set(candidates)
    mask = df.apply(_is_primary, axis=1)
    return df[~mask].reset_index(drop=True)

def export_all(basename: str='mtbls_reuse', combined_df: pd.DataFrame | None=None, study_epmc_df: pd.DataFrame | None=None, study_springer_df: pd.DataFrame | None=None, study_wiley_df: pd.DataFrame | None=None, study_scidir_df: pd.DataFrame | None=None, study_pmc_df: pd.DataFrame | None=None, mtbls_metadata_df: pd.DataFrame | None=None) -> str:
    """Write available source tables, Combined and metadata to one Excel workbook.

    Skip empty source tables. Add a date-and-time suffix to basename, print the
    written path and return it. mtbls_metadata_df is the full accession metadata
    table, including accessions without a retained publication match.
    """
    timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    xlsx_path = f'{basename}_{timestamp}.xlsx'
    with pd.ExcelWriter(xlsx_path, engine='xlsxwriter') as xw:
        if study_epmc_df is not None and (not study_epmc_df.empty):
            study_epmc_df.to_excel(xw, sheet_name='EuropePMC', index=False)
        if study_springer_df is not None and (not study_springer_df.empty):
            study_springer_df.to_excel(xw, sheet_name='Springer', index=False)
        if study_wiley_df is not None and (not study_wiley_df.empty):
            study_wiley_df.to_excel(xw, sheet_name='Wiley', index=False)
        if study_scidir_df is not None and (not study_scidir_df.empty):
            study_scidir_df.to_excel(xw, sheet_name='ScienceDirect', index=False)
        if study_pmc_df is not None and (not study_pmc_df.empty):
            study_pmc_df.to_excel(xw, sheet_name='PMC', index=False)
        if combined_df is not None and (not combined_df.empty):
            combined_df.to_excel(xw, sheet_name='Combined', index=False)
        if mtbls_metadata_df is not None and (not mtbls_metadata_df.empty):
            mtbls_metadata_df.to_excel(xw, sheet_name='MTBLS_metadata', index=False)
    print(f'Wrote Excel to {xlsx_path}')
    return xlsx_path

def main_collect_all(limit_mtbls: int | None=None, limit_mw: int | None=None, limit_gnps: int | None=None, per_id_limit: int | None=None, basename: str='mtbls_mw_gnps_reuse', sleep_between=(0.4, 0.8), pmc: str='on'):
    """Search the five sources for MTBLS candidates and export their records.

    limit_mtbls caps accessions; per_id_limit caps records retained per accession
    and source without removing source-specific retrieval limits. Fetch metadata,
    save intermediate tables under temp/, fill blank Springer details, filter
    reference DOIs and combine repeated candidates before the final export.

    limit_mw and limit_gnps are unused compatibility arguments. pmc is also
    retained for compatibility: PMC is always queried. sleep_between gives the
    random pause range passed to the source collectors.
    """
    Path('temp').mkdir(parents=True, exist_ok=True)
    print('\nCollecting study IDs from MetaboLights...', end='')
    mtbls_ids = fetch_mtbls_ids()[:limit_mtbls] if limit_mtbls else fetch_mtbls_ids()
    # Only MetaboLights accessions are passed to the literature collectors.



    study_ids = mtbls_ids
    print('done')
    print(f'Found {len(mtbls_ids)} MTBLS studies')

    # Save intermediate metadata and source results under temp/ during collection.








    study_metadata = get_study_metadata(study_ids)
    metadata_raw = pd.DataFrame(study_metadata)
    metadata_raw.to_excel(f'temp/{basename}_metadata.xlsx')
    mtbls_metadata = metadata_raw.T
    if 'MTBLS.Accession' in mtbls_metadata:
        mtbls_metadata = mtbls_metadata.loc[mtbls_metadata['MTBLS.Accession'].notna()].reset_index(drop=True)
    else:
        mtbls_metadata = pd.DataFrame()
    print(f'Collected metadata for {len(study_metadata)} studies')
    epmc_df = collect_europe_pmc(study_ids=study_ids, study_metadata=study_metadata, per_id_limit=per_id_limit, sleep_between=sleep_between)
    epmc_df.to_excel(f'temp/{basename}_europe_pmc.xlsx')
    springer_df = collect_springer_rss(study_ids=study_ids, study_metadata=study_metadata, per_id_limit=per_id_limit, sleep_between=sleep_between)
    springer_df = enrich_springer_rows_with_bibliography(springer_df)
    springer_df.to_excel(f'temp/{basename}_springer.xlsx')
    wiley_df = collect_wiley_search_rss(study_ids=study_ids, study_metadata=study_metadata, per_id_limit=per_id_limit, sleep_between=sleep_between)
    wiley_df.to_excel(f'temp/{basename}_wiley.xlsx')
    scidir_df = collect_sciencedirect_api(study_ids=study_ids, study_metadata=study_metadata, per_id_limit=per_id_limit, sleep_between=sleep_between)
    scidir_df.to_excel(f'temp/{basename}_sci_dir.xlsx')
    pmc_df = collect_pmc_esearch(study_ids=study_ids, study_metadata=study_metadata, per_id_limit=per_id_limit, sleep_between=sleep_between, efetch_batch_size=20)
    pmc_df.to_excel(f'temp/{basename}_pmc.xlsx')
    parts_list = [epmc_df, springer_df, wiley_df, scidir_df, pmc_df]
    combined = pd.concat(parts_list, ignore_index=True)
    combined = exclude_primary_publications(combined)
    combined = dedupe_combined(combined)
    combined.to_excel(f'temp/{basename}_combined.xlsx')
    return export_all(basename=basename, combined_df=combined, study_epmc_df=epmc_df, study_springer_df=springer_df, study_wiley_df=wiley_df, study_scidir_df=scidir_df, study_pmc_df=pmc_df, mtbls_metadata_df=mtbls_metadata)


def _positive_int(value: str) -> int:
    """Accept only positive integer limits; omission, rather than zero, means no extra cap."""
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError('must be a positive integer')
    return number


def main(argv: list[str] | None = None) -> str:
    """Read command-line options before starting collection; --help makes no requests."""
    p = argparse.ArgumentParser(description='Collect candidate publications mentioning MTBLS accessions and export to Excel.')
    p.add_argument('--limit_mtbls', type=_positive_int, default=None, help='Limit MTBLS studies searched (default: all public MTBLS studies)')
    # No Workbench or GNPS command-line options are enabled in this release.


    p.add_argument('--per_id_limit', type=_positive_int, default=None, help='Cap hits per study per literature source (default: existing source limits)')
    p.add_argument('--basename', type=str, default='mtbls_mw_gnps_reuse', help='Base filename for output (default: %(default)s)')
    args = p.parse_args(argv)
    return main_collect_all(limit_mtbls=args.limit_mtbls, per_id_limit=args.per_id_limit, basename=args.basename)


if __name__ == '__main__':
    print(main())
