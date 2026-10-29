"""Collect Springer RSS search results and optional DOI-based details.

Read one feed per dataset identifier. Convert its entries to the shared
publication columns, then use the Springer metadata API to fill blank
author or publication-source fields when a DOI and API key are available.
Candidates require manual screening before counting as data reuse.
"""

import random
import time
from typing import List
from urllib.parse import quote_plus

import feedparser
import pandas as pd
import requests
from rich.progress import track

from collect_study_metadata import get_study_metadata
from collect_utils import _authors_joined, _extract_year, _extract_doi_from_text, _strip_html, _load_config

_cfg = _load_config()
SPRINGER_API_KEY = _cfg.get('SPRINGER_API_KEY', '')

SESSION = requests.Session()
SESSION.headers.update({'User-Agent': 'mtbls-mw-gnps-reuse-bot (+https://ebi.ac.uk)'})
DEFAULT_TIMEOUT = 30

def build_springer_rss_url(query: str, newest_first: bool = True) -> str:
    """Build the RSS search URL, sorting by newest date or relevance as requested."""
    q = quote_plus(query)
    sort = 'newestFirst' if newest_first else 'relevance'
    return f'https://link.springer.com/search.rss?new-search=true&query={q}&sortBy={sort}'

def entry_to_record_springer(study_id: str, entry) -> dict:
    """Convert one Springer feed entry to the shared publication columns.

    Prefer the provider's publication date, then the feed's published and
    updated dates. Prefer the supplied DOI field, then search other entry text
    for a DOI. Missing fields remain empty.
    """
    title = entry.get('title', '')
    link = entry.get('link', '')
    summary = entry.get('summary', '')
    published = entry.get('published') or entry.get('updated') or ''
    updated = entry.get('updated', '')
    prism_pubname = entry.get('prism_publicationname') or entry.get('prism:publicationName') or ''
    prism_date = entry.get('prism_publicationdate') or entry.get('prism:publicationDate') or ''
    prism_doi = entry.get('prism_doi') or entry.get('prism:doi') or ''
    dc_ident = entry.get('dc_identifier') or entry.get('dc:identifier') or ''
    authors = _authors_joined(entry)
    paper_doi = str(prism_doi).strip() or _extract_doi_from_text(dc_ident, link, title, summary)
    year = _extract_year(prism_date, published, updated)
    return {
        'Source': 'Springer',
        'Study ID': study_id,
        'Match.Title': title,
        'Match.Authors': authors,
        'Match.Journal': prism_pubname,
        'Match.Year': year,
        'Match.DOI': paper_doi,
        'Match.PublicationType': '',
        'Match.PMID': '',
        'Match.PMCID': '',
        'Match.URL': link,
        'Match.Grant': '',
        'Match.Abstract': _strip_html(summary),
        'Match.OpenAccess': '',
    }

def collect_springer_rss(study_ids: List[str], study_metadata=None, per_id_limit=None, sleep_between=(0.4, 1.0)):
    """Read one RSS feed per identifier and attach dataset metadata.

    per_id_limit caps retained entries from that feed. There is no pagination
    or follow-up feed request. Fetch dataset metadata when none is supplied,
    and pause within sleep_between after each identifier.
    """
    if not study_metadata:
        study_metadata = get_study_metadata(study_ids)

    rows = []
    for study_id in track(study_ids, description='Springer'):
        feed = feedparser.parse(build_springer_rss_url(study_id, newest_first=True))
        for i, entry in enumerate(feed.entries, 1):
            rows.append({**entry_to_record_springer(study_id, entry), **study_metadata.get(study_id, {})})
            if per_id_limit and i >= per_id_limit:
                break
        time.sleep(random.uniform(*sleep_between))
    return pd.DataFrame(rows)

def springer_meta_by_doi(doi: str) -> dict:
    """Look up publication-source and author details for one DOI.

    Use the first API record. Return an empty dictionary when the DOI or key
    is absent, the request times out, the status is not 200 or no record exists.
    Other request or response-parsing errors are not handled here.
    """
    if not doi or not SPRINGER_API_KEY:
        return {}
    url = 'https://api.springernature.com/meta/v2/json'
    params = {'q': f'doi:{doi}', 'api_key': SPRINGER_API_KEY, 'p': 1}
    try:
        r = SESSION.get(url, params=params, timeout=DEFAULT_TIMEOUT)
    except requests.Timeout:
        return {}
    if r.status_code != 200:
        return {}
    data = r.json() or {}
    records = data.get('records') or []
    if not records:
        return {}
    rec = records[0]
    journal = rec.get('publicationName', '') or rec.get('journal', '')
    authors_list = []
    for c in rec.get('creators') or []:
        if isinstance(c, dict):
            if c.get('creator'):
                authors_list.append(str(c['creator']).strip())
            else:
                gn = (c.get('givenName') or '').strip()
                fn = (c.get('familyName') or '').strip()
                nm = ', '.join([fn, gn]).strip(', ').strip()
                if nm:
                    authors_list.append(nm)
        elif isinstance(c, str):
            authors_list.append(c.strip())
    return {'journal': journal, 'authors': '; '.join([a for a in authors_list if a])}

def enrich_springer_rows_with_bibliography(df: pd.DataFrame, sleep_between=(0.2, 0.5)) -> pd.DataFrame:
    """Fill blank-string source or author fields in Springer rows by DOI lookup.

    Update the supplied table in place. Existing nonempty fields are retained;
    rows without a DOI are skipped. Pause between lookups.
    """
    if df is None or df.empty:
        return df
    mask = (df['Source'] == 'Springer') & ((df['Match.Journal'] == '') | (df['Match.Authors'] == ''))
    if not mask.any():
        return df
    for idx in track(df[mask].index, description='Springer - missing metadata retrieval by DOI search'):
        doi = (df.at[idx, 'Match.DOI'] or '').strip()
        if not doi:
            continue
        meta = springer_meta_by_doi(doi)
        if df.at[idx, 'Match.Journal'] == '' and meta.get('journal'):
            df.at[idx, 'Match.Journal'] = meta['journal']
        if df.at[idx, 'Match.Authors'] == '' and meta.get('authors'):
            df.at[idx, 'Match.Authors'] = meta['authors']
        time.sleep(random.uniform(*sleep_between))
    return df

# The example below also uses Workbench and MassIVE IDs.
# Use main_mtbls_mw_gnps_reuse.py for the released MTBLS-only workflow.
if __name__ == '__main__':
    no_of_studies = [1, 1, 10]
    mtbls_ids = [f'MTBLS{n}' for n in range(1, no_of_studies[0] + 1)]
    mw_ids = [f'ST{str(n).zfill(6)}' for n in range(1, no_of_studies[1] + 1)]
    msv_id_key = ["96", "97", "98"]
    msv_ids = [f'MSV0000{msv_id_key[random.randint(0, len(msv_id_key)-1)]}{str(n).zfill(3)}' for n in range(1, no_of_studies[2] + 1)]
    study_ids = mtbls_ids + mw_ids + msv_ids


    df = collect_springer_rss(study_ids)
    df = enrich_springer_rows_with_bibliography(df)
    print(df.head(2).to_dict(orient='records'))
