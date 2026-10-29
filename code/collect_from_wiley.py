"""Collect candidate publications from Wiley search RSS feeds.

Read one feed per dataset identifier and copy its publication details to
the shared Source, Study ID and Match.* columns. The main workflow supplies
MTBLS identifiers; manual screening determines which matches are reuse.
"""

import random
import time
from typing import List
from urllib.parse import quote as _quote

import feedparser
import pandas as pd
from rich.progress import track

from collect_study_metadata import get_study_metadata
from collect_utils import _extract_year, _extract_doi_from_text, _strip_html, _authors_joined

def build_wiley_search_rss(study_id: str, content: str = 'articlesChapters') -> str:
    """Build a Wiley RSS URL with the identifier and content filter encoded in query.

    The default articlesChapters filter requests article and chapter content.
    This function only constructs the URL; it does not make a request.
    """
    inner = f'&AllField={study_id}&content={content}&target=default'
    inner_encoded = _quote(inner, safe='')
    return f'https://onlinelibrary.wiley.com/action/showFeed?type=search&feed=rss&query={inner_encoded}'

def entry_to_record_wiley(study_id: str, entry) -> dict:
    """Convert one Wiley feed entry to the shared publication columns.

    Prefer the cover date, then published and updated feed dates, for the year.
    Read the DOI field before searching other entry text. Use entry content
    for the abstract when available, otherwise the summary. The source label
    is taken from the summary text before its first comma.
    """
    title = entry.get('title', '')
    link = entry.get('link', '').split('?mi=')[0]
    summary = entry.get('summary', '')
    published = entry.get('published') or entry.get('updated') or ''
    updated = entry.get('updated', '')
    prism_date = entry.get('prism_coverdate', '')
    prism_doi = entry.get('prism_doi') or entry.get('prism:doi') or ''
    dc_ident = entry.get('dc_identifier', '')
    authors = entry.get('author', '')
    authors = authors.replace('\n', '').strip() if isinstance(authors, str) else _authors_joined(entry)
    paper_doi = str(prism_doi).strip() or _extract_doi_from_text(dc_ident, link, title, summary)
    year = _extract_year(prism_date, published, updated)

    abstract_text = ''
    if hasattr(entry, 'content') and entry.content:
        fc = entry.content[0]
        if isinstance(fc, dict):
            abstract_text = fc.get('value', '').strip()
        elif hasattr(fc, 'value'):
            abstract_text = getattr(fc, 'value', '').strip()
    abstract_text = _strip_html(abstract_text) if abstract_text else _strip_html(summary)

    return {
        'Source': 'Wiley',
        'Study ID': study_id,
        'Match.Title': title,
        'Match.Authors': authors,
        'Match.Journal': summary.split(',')[0],
        'Match.Year': year,
        'Match.DOI': paper_doi,
        'Match.PublicationType': '',
        'Match.PMID': '',
        'Match.PMCID': '',
        'Match.URL': link,
        'Match.Grant': '',
        'Match.Abstract': abstract_text,
        'Match.OpenAccess': '',
    }

def collect_wiley_search_rss(study_ids: List[str], study_metadata=None, per_id_limit=None, sleep_between=(0.4, 1.0)):
    """Read one Wiley feed per identifier and attach dataset metadata.

    per_id_limit caps retained entries in that feed; no additional pages are
    requested. Fetch metadata when none is supplied and pause within
    sleep_between after each identifier.
    """
    if not study_metadata:
        study_metadata = get_study_metadata(study_ids)

    rows = []
    for study_id in track(study_ids, description='Wiley'):
        url = build_wiley_search_rss(study_id)
        feed = feedparser.parse(url)
        for i, entry in enumerate(feed.entries, 1):
            rows.append({**entry_to_record_wiley(study_id, entry), **study_metadata.get(study_id, {})})
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
    msv_ids = [f'MSV0000{msv_id_key[random.randint(0, len(msv_id_key) - 1)]}{str(n).zfill(3)}' for n in
               range(1, no_of_studies[2] + 1)]
    study_ids = mtbls_ids + mw_ids + msv_ids


    df = collect_wiley_search_rss(study_ids)
    print(df.head(3).to_dict(orient='records'))
