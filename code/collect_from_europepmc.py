"""Search Europe PMC for candidate papers mentioning dataset identifiers.

Read one API page per identifier and convert the returned publication
details to the shared Source, Study ID and Match.* columns. Search matches
still require manual screening before they can count as data reuse.
"""

import random
import time
from typing import List, Dict, Any

import pandas as pd
import requests
from rich.progress import track

from collect_study_metadata import get_study_metadata

SESSION = requests.Session()
SESSION.headers.update({'User-Agent': 'mtbls-mw-gnps-reuse-bot (+https://ebi.ac.uk)'})
DEFAULT_TIMEOUT = 30

def search_europe_pmc(id_str: str, max_results: int = 1000) -> List[Dict[str, Any]]:
    """Request one Europe PMC result page for a quoted identifier.

    max_results sets pageSize, with a default of 1,000. No later pages are
    requested. Return an empty list for request failures or a non-200 status.
    """
    url = 'https://www.ebi.ac.uk/europepmc/webservices/rest/search'
    params = {
        'query': f'"{id_str}"',
        'format': 'json',
        'pageSize': max_results,
        'resultType': 'core',
        'sort': 'P_PDATE_D desc',
    }
    try:
        r = SESSION.get(url, params=params, timeout=DEFAULT_TIMEOUT)
    except requests.RequestException:
        return []
    if r.status_code == 200:
        return r.json().get('resultList', {}).get('result', []) or []
    return []

def _oa_flag(val) -> str:
    """Convert supported open-access indicators to 'Yes', otherwise return empty text."""
    if isinstance(val, bool):
        return 'Yes' if val else ''
    s = str(val or '').strip().lower()
    return 'Yes' if s in ('true', 'y', 'yes', '1') else ''

def entry_to_record_europepmc(study_id: str, paper: dict) -> dict:
    """Copy one Europe PMC result into the shared publication columns.

    Use the reported pubYear without recalculating it. Keep the provider's
    DOI, PMID, PMCID, abstract, author names and full-text links where supplied.
    """
    paper_doi = (paper.get('doi') or '').strip()
    return {
        'Source': 'EuropePMC',
        'Study ID': study_id,
        'Match.Title': paper.get('title') or '',
        'Match.Authors': paper.get('authorString') or '',
        'Match.Journal': paper.get('journalInfo', {}).get('journal', {}).get('title', '')
                          or paper.get('bookOrReportDetails', {}).get('publisher', ''),
        'Match.Year': paper.get('pubYear') or '',
        'Match.DOI': paper_doi,
        'Match.PublicationType': '; '.join((pt for pt in paper.get('pubTypeList', {}).get('pubType', []) if pt)),
        'Match.PMID': paper.get('pmid') or '',
        'Match.PMCID': paper.get('pmcid') or '',
        'Match.URL': '; '.join((u.get('url') for u in paper.get('fullTextUrlList', {}).get('fullTextUrl', []) if u.get('url'))),
        'Match.Grant': '; '.join((g.get('agency') for g in paper.get('grantsList', {}).get('grant', []) if g.get('agency'))),
        'Match.Abstract': paper.get('abstractText') or '',
        'Match.OpenAccess': _oa_flag(paper.get('isOpenAccess', '')),
    }

def collect_europe_pmc(study_ids: List[str], study_metadata=None, per_id_limit=None, sleep_between=(0.4, 1.0)):
    """Collect up to one page of 1,000 results for each dataset identifier.

    per_id_limit limits how many returned records are retained; it does not
    request more pages or increase the 1,000-result request. Attach supplied
    dataset metadata, fetching it first when absent, and pause after each ID.
    """
    if not study_metadata:
        study_metadata = get_study_metadata(study_ids)

    out = []
    for study_id in track(study_ids, description='EuropePMC'):
        papers = search_europe_pmc(study_id, max_results=1000)
        for i, paper in enumerate(papers, 1):
            out.append({**entry_to_record_europepmc(study_id, paper), **study_metadata.get(study_id, {})})
            if per_id_limit and i >= per_id_limit:
                break
        time.sleep(random.uniform(*sleep_between))

    return pd.DataFrame(out).reset_index(drop=True)

# The example below also uses Workbench and MassIVE IDs.
# Use main_mtbls_mw_gnps_reuse.py for the released MTBLS-only workflow.
if __name__ == '__main__':
    no_of_studies = [1, 1, 10]
    mtbls_ids = [f'MTBLS{n}' for n in range(1, no_of_studies[0] + 1)]
    mw_ids = [f'ST{str(n).zfill(6)}' for n in range(1, no_of_studies[1] + 1)]
    msv_id_key = ["96", "97", "98"]
    msv_ids = [f'MSV0000{msv_id_key[random.randint(0, len(msv_id_key)-1)]}{str(n).zfill(3)}' for n in range(1, no_of_studies[2] + 1)]
    study_ids = mtbls_ids + mw_ids + msv_ids


    df = collect_europe_pmc(study_ids)
    print(df.head(2).to_dict(orient='records'))
