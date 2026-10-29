"""Search ScienceDirect for candidate papers mentioning dataset identifiers.

Request one result page for each identifier, then retrieve article abstracts
by DOI where available. Results use the shared Source, Study ID and Match.*
columns. A match is a candidate for manual review, not confirmed data reuse.
The main workflow supplies only MTBLS identifiers.
"""

import os
import random
import time
from typing import List
from urllib.parse import quote_plus as _qp

import pandas as pd
import requests
from rich.progress import track

from collect_study_metadata import get_study_metadata
from collect_utils import _extract_year, _extract_doi_from_text, _authors_joined, _strip_html, _load_config

_cfg = _load_config()
ELSEVIER_API_KEY = _cfg.get('ELSEVIER_API_KEY', '')
ELSEVIER_INSTTOKEN = os.getenv('ELSEVIER_INSTTOKEN', '')

SESSION = requests.Session()
SESSION.headers.update({'User-Agent': 'mtbls-mw-gnps-reuse-bot (+https://ebi.ac.uk)'})
DEFAULT_TIMEOUT = 30

def _http_headers() -> dict:
    """Build request headers with the configured Elsevier API key.

    Include an institutional token only when ELSEVIER_INSTTOKEN is set in the
    environment. The token is not read from the public example configuration.
    """
    h = {'Accept': 'application/json', 'X-ELS-APIKey': ELSEVIER_API_KEY}
    if ELSEVIER_INSTTOKEN:
        h['X-ELS-Insttoken'] = ELSEVIER_INSTTOKEN
    return h

def search_sciencedirect(study_id: str, max_results: int = 100) -> list[dict]:
    """Request one ScienceDirect result page for a quoted dataset identifier.

    max_results sets the API count parameter; its default is 100. No later
    pages are requested. Return an empty list when the API key is absent, a
    request fails or the response status is not 200.
    """
    if not ELSEVIER_API_KEY:
        return []
    url = 'https://api.elsevier.com/content/search/sciencedirect'
    params = {'query': f'"{study_id}"', 'count': max_results, 'sort': '-date'}
    try:
        r = SESSION.get(url, headers=_http_headers(), params=params, timeout=DEFAULT_TIMEOUT)
    except requests.RequestException:
        return []
    if r.status_code != 200:
        return []
    return r.json().get('search-results', {}).get('entry', []) or []

def fetch_scidir_article_abstract_by_doi(doi: str) -> str:
    """Request an article's abstract by DOI and remove HTML tags.

    Return empty text when the DOI or API key is absent, the request fails,
    the response status is not 200 or no abstract is supplied.
    """
    if not doi or not ELSEVIER_API_KEY:
        return ''
    url = f'https://api.elsevier.com/content/article/doi/{_qp(doi)}'
    try:
        r = SESSION.get(url, headers=_http_headers(), params={'httpAccept': 'application/json'}, timeout=DEFAULT_TIMEOUT)
    except requests.RequestException:
        return ''
    if r.status_code != 200:
        return ''
    data = r.json()
    desc = data.get('full-text-retrieval-response', {}).get('coredata', {}).get('dc:description', '')
    return _strip_html(desc)

def entry_to_record_sciencedirect(study_id: str, entry: dict) -> dict:
    """Convert one search result to the shared publication columns.

    Use the cover date for the year. Read the DOI from the provider's DOI field,
    then search its identifier, link and title fields if needed. Retrieve the
    abstract separately and construct an article link from PII or DOI.
    """
    title = entry.get('dc:title', '') or entry.get('title', '')
    journal = entry.get('prism:publicationName', '')
    date = entry.get('prism:coverDate', '')
    year = _extract_year(date)
    doi = entry.get('prism:doi', '') or _extract_doi_from_text(entry.get('dc:identifier', ''), entry.get('link', ''), title)
    authors = _authors_joined(entry)

    abstract_text = fetch_scidir_article_abstract_by_doi(doi)
    open_access = entry.get('openaccess', '')
    open_access = 'Yes' if str(open_access).strip() not in ('', '0', 'false', 'False') else ''

    pii = entry.get('pii', '') or entry.get('prism:pii', '')
    if pii:
        url = f'https://www.sciencedirect.com/science/article/pii/{pii}'
    else:
        url = f'https://doi.org/{doi}' if doi else ''

    return {
        'Source': 'ScienceDirectAPI',
        'Study ID': study_id,
        'Match.Title': title,
        'Match.Authors': authors,
        'Match.Journal': journal,
        'Match.Year': year,
        'Match.DOI': doi,
        'Match.PublicationType': '',
        'Match.PMID': '',
        'Match.PMCID': '',
        'Match.URL': url,
        'Match.Grant': '',
        'Match.Abstract': abstract_text,
        'Match.OpenAccess': open_access,
    }

def collect_sciencedirect_api(study_ids: List[str], study_metadata=None, per_id_limit=None, sleep_between=(0.4, 1.0)):
    """Collect one result page per identifier and attach dataset metadata.

    Use the supplied metadata dictionary, or fetch metadata when it is empty.
    per_id_limit sets both the requested page size and the maximum retained
    records for each identifier; otherwise request 100. Pause within
    sleep_between after each identifier. Provider limits still apply.
    """
    if not study_metadata:
        study_metadata = get_study_metadata(study_ids)
    rows = []

    for study_id in track(study_ids, description='ScienceDirect'):
        entries = search_sciencedirect(study_id, max_results=per_id_limit or 100)
        if entries and isinstance(entries, list) and (not (isinstance(entries[0], dict) and 'error' in entries[0])):
            for i, entry in enumerate(entries, 1):
                rows.append({**entry_to_record_sciencedirect(study_id, entry), **study_metadata.get(study_id, {})})
                if per_id_limit and i >= per_id_limit:
                    break
        time.sleep(random.uniform(*sleep_between))
    return pd.DataFrame(rows)

# The example below also uses Workbench and MassIVE IDs.
# Use main_mtbls_mw_gnps_reuse.py for the released MTBLS-only workflow.
if __name__ == '__main__':
    no_of_studies = [1, 1, 10]
    mtbls_ids = [f'MTBLS{n}' for n in range(1, no_of_studies[0] + 1)]
    mw_ids = [f'ST{str(n).zfill(6)}' for n in range(1, no_of_studies[1] + 1)]
    msv_id_key = ["96", "97", "98"]
    msv_ids = [f'MSV0000{msv_id_key[random.randint(0, len(msv_id_key)-1)]}{str(n).zfill(3)}' for n in range(1, no_of_studies[2] + 1)]
    study_ids = mtbls_ids + mw_ids + msv_ids


    df = collect_sciencedirect_api(study_ids)
    print(df.head(2).to_dict(orient='records'))
