"""Search PubMed Central and retrieve publication details and abstracts.

ESearch returns one page of article identifiers. ESummary supplies publication
details, and EFetch retrieves article XML in batches for abstract extraction.
The main workflow supplies MTBLS identifiers and retains candidates for
manual review. Presence in PMC alone is not evidence of active data reuse.
"""

import re
import random
import time
from typing import Dict, Iterable, List

import pandas as pd
import requests
from rich.progress import track

from collect_study_metadata import get_study_metadata
from collect_utils import _load_config

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
DEFAULT_TIMEOUT = 30

_cfg = _load_config()
PMC_API_KEY = _cfg.get('PMC_API_KEY', '')
TOOL_NAME = _cfg.get('NCBI_TOOL_NAME', 'mtbls-mw-gnps-reuse-bot')
TOOL_EMAIL = _cfg.get('NCBI_TOOL_EMAIL', 'your.email@ebi.ac.uk')

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "mtbls-mw-gnps-reuse-bot (+https://ebi.ac.uk)"})

def _safe_get(url: str, params: dict, retries: int = 4, timeout: int = DEFAULT_TIMEOUT) -> requests.Response | None:
    """Try an NCBI request up to retries times, waiting longer after failures.

    Retry request and HTTP errors. Return the successful response or None
    after all attempts fail. timeout is the limit for each individual request.
    """
    for i in range(retries):
        try:
            r = SESSION.get(url, params=params, timeout=timeout)
            if r.status_code >= 500:
                raise requests.HTTPError(f"Server {r.status_code}")
            r.raise_for_status()
            return r
        except requests.RequestException:
            sleep_s = (2 ** i) + random.random()
            time.sleep(sleep_s)
    return None

def _chunked(it: Iterable[str], size: int) -> Iterable[list[str]]:
    """Yield lists of at most size items, including any shorter final list."""
    buf = []
    for x in it:
        buf.append(x)
        if len(buf) >= size:
            yield buf
            buf = []
    if buf:
        yield buf

def _extract_year_from_pubdate(pubdate: str) -> str:
    """Find a standalone four-digit year in a PMC date string, or return empty text."""
    for piece in str(pubdate).split():
        if piece.isdigit() and len(piece) == 4:
            return piece
    return ""

def _strip_html(text: str) -> str:
    """Remove XML or HTML tags and reduce repeated whitespace in abstract text."""
    if not isinstance(text, str):
        return ""
    text = re.sub("<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()

def pmc_esearch(term: str, retmax: int = 1000) -> list[str]:
    """Request one page of PMC article identifiers for the supplied search term.

    The default retmax is 1,000. The caller passes the identifier directly;
    this function does not add quotes or request subsequent result pages.
    """
    params = {
        "db": "pmc",
        "term": term,
        "retmode": "json",
        "retmax": retmax,
        "sort": "date",
        "tool": TOOL_NAME,
        "email": TOOL_EMAIL,
        "api_key": PMC_API_KEY,
    }
    r = _safe_get(f"{EUTILS}/esearch.fcgi", params=params)
    if not r:
        return []
    return r.json().get("esearchresult", {}).get("idlist", []) or []

def pmc_esummary(uids: list[str]) -> list[dict]:
    """Retrieve publication summaries for the supplied PMC database identifiers."""
    if not uids:
        return []
    params = {"db": "pmc", "id": ",".join(uids), "retmode": "json",
              "tool": TOOL_NAME, "email": TOOL_EMAIL, "api_key": PMC_API_KEY}
    r = _safe_get(f"{EUTILS}/esummary.fcgi", params=params)
    if not r:
        return []
    data = r.json().get("result", {}) or {}
    return [data[k] for k in data.get("uids", []) if isinstance(data.get(k), dict)]

def pmc_efetch_batch(pmcids: list[str]) -> Dict[str, str]:
    """Retrieve article XML and return abstract text indexed by PMCID.

    Only article blocks containing a recognised PMCID are retained. Articles
    with a PMCID but no abstract receive an empty abstract string.
    """
    if not pmcids:
        return {}
    params = {"db": "pmc", "id": ",".join(pmcids), "retmode": "xml",
              "tool": TOOL_NAME, "email": TOOL_EMAIL, "api_key": PMC_API_KEY}
    r = _safe_get(f"{EUTILS}/efetch.fcgi", params=params, timeout=max(DEFAULT_TIMEOUT, 60))
    if not r:
        return {}
    xml = r.text
    articles = re.findall(r"(<article[\s\S]*?</article>)", xml, flags=re.IGNORECASE)
    out: Dict[str, str] = {}
    for art in articles:
        m_id = re.search(r'<article-id[^>]+pub-id-type="pmcid"[^>]*>(PMC\d+)</article-id>', art, flags=re.IGNORECASE,)
        if not m_id:
            continue
        pmcid = m_id.group(1)
        m_abs = re.search(r"<abstract[^>]*>([\s\S]*?)</abstract>", art, flags=re.IGNORECASE)
        abs_text = _strip_html(m_abs.group(1)) if m_abs else ""
        out[pmcid] = abs_text
    return out

def _pmc_doc_to_row(study_id: str, doc: dict, abstract_map: Dict[str, str]) -> dict:
    """Convert one PMC summary and its abstract to the shared columns.

    Use pubdate when populated, otherwise epubdate, to obtain the year.
    Match.OpenAccess is set to Yes when a PMCID is present; this flag does not
    check the article's licence or establish that a dataset was reused.
    """
    title = doc.get("title", "")
    journal = doc.get("fulljournalname") or doc.get("source", "")
    pubdate = doc.get("pubdate") or doc.get("epubdate", "")
    year = _extract_year_from_pubdate(pubdate)
    doi, pmcid, pmid = "", "", ""
    for id_obj in doc.get("articleids", []) or []:
        if id_obj.get("idtype") == "doi":
            doi = id_obj.get("value", "")
        elif id_obj.get("idtype") == "pmcid":
            pmcid = id_obj.get("value", "")
        elif id_obj.get("idtype") == "pmid":
            pmid = id_obj.get("value", "")
    url = f"https://pmc.ncbi.nlm.nih.gov/articles/{pmcid}/" if pmcid else (f"https://doi.org/{doi}" if doi else "")
    authors = "; ".join([a.get("name") for a in doc.get("authors", []) if a.get("name")])
    abstract_text = abstract_map.get(pmcid, "")
    return {
        "Source": "PMC",
        "Study ID": study_id,
        "Match.Title": title,
        "Match.Authors": authors,
        "Match.Journal": journal,
        "Match.Year": year,
        "Match.DOI": doi,
        "Match.PublicationType": "",
        "Match.PMID": pmid,
        "Match.PMCID": pmcid,
        "Match.URL": url,
        "Match.Grant": "",
        "Match.Abstract": abstract_text,
        "Match.OpenAccess": "Yes" if pmcid else "",
    }

def collect_pmc_esearch(study_ids: List[str], study_metadata=None, per_id_limit=None, sleep_between=(1.0, 2.0), efetch_batch_size: int = 20,) -> pd.DataFrame:
    """Search once per dataset identifier and retrieve summaries and abstracts.

    The search requests at most the default 1,000 article identifiers.
    per_id_limit can reduce retained summaries but does not add result pages.
    Fetch abstracts in groups of efetch_batch_size and pause between batches
    and identifiers. Attach supplied dataset metadata, fetching it when absent.
    """
    if not study_metadata:
        study_metadata = get_study_metadata(study_ids)
    rows: list[dict] = []
    for sid in track(study_ids, description='PMC'):
        uids = pmc_esearch(sid)
        docs = pmc_esummary(uids)
        if per_id_limit:
            docs = docs[:per_id_limit]
        pmcids = []
        for d in docs:
            for id_obj in d.get("articleids", []) or []:
                if id_obj.get("idtype") == "pmcid" and id_obj.get("value"):
                    pmcids.append(id_obj["value"])
        abstract_map: Dict[str, str] = {}
        for chunk in _chunked(pmcids, efetch_batch_size):
            abstract_map.update(pmc_efetch_batch(chunk))
            time.sleep(random.uniform(*sleep_between))
        for d in docs:
            rec = _pmc_doc_to_row(sid, d, abstract_map)
            rows.append({**rec, **study_metadata.get(sid, {})})
        time.sleep(random.uniform(*sleep_between))
    return pd.DataFrame(rows)

# The example below also uses Workbench and MassIVE IDs.
# Use main_mtbls_mw_gnps_reuse.py for the released MTBLS-only workflow.
if __name__ == "__main__":
    no_of_studies = [1, 1, 10]
    mtbls_ids = [f'MTBLS{n}' for n in range(1, no_of_studies[0] + 1)]
    mw_ids = [f'ST{str(n).zfill(6)}' for n in range(1, no_of_studies[1] + 1)]
    msv_id_key = ["96", "97", "98"]
    msv_ids = [f'MSV0000{msv_id_key[random.randint(0, len(msv_id_key)-1)]}{str(n).zfill(3)}' for n in range(1, no_of_studies[2] + 1)]
    study_ids = mtbls_ids + mw_ids + msv_ids


    df = collect_pmc_esearch(study_ids)
    print(f"Collected {len(df)} records from PMC.")
