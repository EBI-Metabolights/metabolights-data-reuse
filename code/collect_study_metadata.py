"""Retrieve dataset identifiers and descriptive repository metadata.

The released collection workflow uses fetch_mtbls_ids and MetaboLights
metadata. Helpers for Metabolomics Workbench and GNPS/MassIVE are retained
from earlier work but are not called by the MTBLS-only search. Running this
file directly starts the older Workbench example at the bottom.
"""

import json
import re
import time
from typing import List, Dict, Any
import requests
from rich.progress import track
from collect_utils import _extract_doi_from_text, _flatten_dict, _compact_join
SESSION = requests.Session()
SESSION.headers.update({'User-Agent': 'mtbls-mw-gnps-reuse-bot (+https://ebi.ac.uk)'})
DEFAULT_TIMEOUT = 30
DOI_RE = re.compile('10\\.\\d{4,9}/[-._;()/:A-Z0-9]+', re.I)
MTBLS_ID_PATTERN = re.compile('^MTBLS\\d+$')
MW_STUDY_LIST_URL = 'https://www.metabolomicsworkbench.org/rest/study/study_id/ST/available'
MW_ID_PATTERN = re.compile('^ST\\d{6}$')
MW_MWTAB_URL_TMPL = 'https://www.metabolomicsworkbench.org/rest/study/study_id/{study_id}/mwtab'
GNPS_ID_PATTERN = re.compile('^MSV\\d{9}$')
GNPS_SEARCH_URL = 'https://massive.ucsd.edu/ProteoSAFe/datasets_json.jsp'
GNPS_DATASET_URL_TMPL = 'https://massive.ucsd.edu/ProteoSAFe/dataset.jsp?accession={msv}'

def fetch_mtbls_ids() -> List[str]:
    """Return the accession list supplied by the MetaboLights public endpoint.

    Return an empty list for a non-200 response. Network and JSON-reading
    errors are not caught by this function.
    """
    url = 'https://www.ebi.ac.uk/metabolights/ws/studies'
    r = SESSION.get(url, timeout=DEFAULT_TIMEOUT)
    if r.status_code == 200:
        return r.json().get('content', [])
    return []

def get_mtbls_metadata(mtbls_id: str) -> dict:
    """Read the first study record returned for a MetaboLights accession.

    Return MTBLS.* fields for title, description, dates, study descriptors,
    factors, assays, technologies, platforms, contacts, reference publications
    and the study link. Return an empty dictionary for a non-200 response.
    Organism text is scanned internally but is not added to the returned fields.
    """
    url = f'https://www.ebi.ac.uk/metabolights/ws/studies/{mtbls_id}'
    r = SESSION.get(url, timeout=DEFAULT_TIMEOUT)
    meta = {}
    if r.status_code != 200:
        return meta
    data = r.json()
    inv = data.get('isaInvestigation', {}) or {}
    study = (inv.get('studies') or [{}])[0]
    meta['MTBLS.Accession'] = mtbls_id
    meta['MTBLS.StudyTitle'] = study.get('title', '')
    meta['MTBLS.StudyDescription'] = study.get('description', '')
    meta['MTBLS.SubmissionDate'] = study.get('submissionDate', '') or data.get('submissionDate', '')
    meta['MTBLS.ReleaseDate'] = study.get('publicReleaseDate', '') or data.get('releaseDate', '')
    descriptors, factors, assays, technologies, platforms, organisms = ([], [], [], [], [], [])
    for sdd in study.get('studyDesignDescriptors') or []:
        av = sdd.get('annotationValue')
        if av:
            descriptors.append(str(av))
    for ff in study.get('factors') or []:
        fn = ff.get('factorName')
        if fn:
            factors.append(str(fn))
    for aa in study.get('assays') or []:
        mt = aa.get('measurementType', {}).get('annotationValue')
        if mt:
            assays.append(str(mt))
        tt = aa.get('technologyType', {}).get('annotationValue')
        if tt:
            technologies.append(str(tt))
        platform = aa.get('technologyPlatform')
        if platform:
            platforms.append(str(platform))
    people = []
    for c in study.get('people') or []:
        ln = c.get('lastName') or ''
        fn = c.get('firstName') or ''
        em = c.get('email') or ''
        org = c.get('affiliation') or ''
        label = ', '.join([x for x in [fn + (' ' if fn and ln else '') + ln, org, em] if x])
        if label.strip():
            people.append(label.strip())
    pubs = study.get('publications') or []
    pub_titles = [p.get('title', '') for p in pubs if p.get('title')]
    pub_dois = [_extract_doi_from_text(p.get('doi', '')) for p in pubs if p.get('doi')]
    pub_pmids = [p.get('pubMedID', '') for p in pubs if p.get('pubMedID')]
    flat = _flatten_dict(study)
    for k, v in flat.items():
        kv = str(v)
        if 'organism' in k.lower() and kv.strip():
            organisms.append(kv)
    meta['MTBLS.Descriptors'] = _compact_join(descriptors)
    meta['MTBLS.Factors'] = _compact_join(factors)
    meta['MTBLS.Assays'] = _compact_join(assays)
    meta['MTBLS.Technologies'] = _compact_join(technologies)
    meta['MTBLS.Platforms'] = _compact_join(platforms)
    meta['MTBLS.Contacts'] = _compact_join(people)
    meta['MTBLS.PublicationTitles'] = _compact_join(pub_titles)
    meta['MTBLS.PublicationDOIs'] = _compact_join(pub_dois)
    meta['MTBLS.PMIDs'] = _compact_join(pub_pmids)
    meta['MTBLS.StudyURL'] = f'https://www.ebi.ac.uk/metabolights/{mtbls_id}'
    return meta

def fetch_mw_ids(timeout: float=DEFAULT_TIMEOUT) -> List[str]:
    """Read Workbench accessions from supported JSON or text responses.

    Remove repeats. JSON accessions keep their first-seen order; text matches
    are sorted by the text parser.
    This helper is retained for earlier work and is not called by the
    MTBLS-only collection workflow.
    """
    r = SESSION.get(MW_STUDY_LIST_URL, timeout=timeout)
    if r.status_code != 200:
        return []

    def _parse_ids_from_text(text: str) -> List[str]:
        """Find complete ST accessions in text and sort the distinct matches.

        Require ST followed by exactly six digits. This parser is not called
        by the released MTBLS-only workflow.
        """
        return sorted(set(re.findall(r'\bST\d{6}\b', text)))
    try:
        data = r.json()
        ids = []
        if isinstance(data, dict):
            for key in data:
                value = data[key]
                if isinstance(value, dict) and 'study_id' in value:
                    study_id = value['study_id']
                    if isinstance(study_id, list):
                        ids.extend([str(x).strip() for x in study_id])
                    else:
                        ids.append(str(study_id).strip())
        elif isinstance(data, list):
            ids = [str(x).strip() for x in data]
        else:
            ids = _parse_ids_from_text(r.text)
    except ValueError:
        ids = _parse_ids_from_text(r.text)
    seen, unique = (set(), [])
    for i in ids:
        if i not in seen:
            seen.add(i)
            unique.append(i)
    return unique

def get_mw_metadata(study_id: str, timeout: float=DEFAULT_TIMEOUT) -> Dict[str, Any]:
    """Read Workbench study details, with older response-repair attempts retained.

    Try the request up to three times after connection errors. If JSON cannot
    be read directly, try joining adjacent objects and removing the known
    Additional sample data lines. This helper is not used by the MTBLS-only run.
    """
    url = MW_MWTAB_URL_TMPL.format(study_id=study_id)
    for attempt in range(3):
        try:
            r = SESSION.get(url, timeout=timeout)
            break
        except requests.exceptions.ConnectionError as e:
            print(f'Connection error for {study_id} (attempt {attempt + 1}/3): {e}')
            time.sleep(2)
    else:
        return {}
    if r.status_code != 200:
        return {}
    try:
        data = r.json()
    except (ValueError, requests.RequestException) as e1:
        try:
            updated_text = re.sub('}\\s*{', '}, {', e1.doc)
            data = json.loads(f'[{updated_text}]')
            data_combined = data[0] if isinstance(data, list) and data else {}
            keys_with_multiple_entries = ['METABOLOMICS_WORKBENCH', 'CHROMATOGRAPHY', 'ANALYSIS', 'MS', 'NMR']
            for mw_entry in data[1:]:
                for key in keys_with_multiple_entries:
                    if key in mw_entry and key in data_combined:
                        for entry_key in mw_entry[key]:
                            if entry_key in data_combined[key]:
                                data_combined[key][entry_key] = '; '.join([data_combined[key][entry_key], mw_entry[key][entry_key]])
                    elif key in mw_entry:
                        data_combined[key] = mw_entry[key]
            data = data_combined
        except (ValueError, requests.RequestException) as e2:
            bad_lines = ['Additional sample data' in ln for ln in e2.doc.splitlines()]
            if any(bad_lines):
                print(f'{study_id} Warning: Skipping known bad lines (Additional sample data) in mwTab JSON')
                cleaned_text = '\n'.join([ln for ln in e2.doc.splitlines() if 'Additional sample data' not in ln])
                updated_cleaned_text = re.sub('},\\n*}', '}\\n}', cleaned_text)
                try:
                    data = json.loads(updated_cleaned_text)
                except (ValueError, requests.RequestException) as e3:
                    print(f'{study_id} Still error after cleaning: {e3}')
                    data = {}
            else:
                print(f'{study_id} Error parsing mwTab JSON: {e2}')
                data = {}

    def section(name: str) -> dict:
        """Find a dictionary section by name, ignoring letter case; otherwise return empty."""
        if isinstance(data, dict):
            for k, v in data.items():
                if isinstance(v, dict) and str(k).upper() == name.upper():
                    return v
        return {}
    meta: Dict[str, Any] = {'MW.Accession': study_id}
    study = section('STUDY')
    if study:
        meta.update({'MW.StudyTitle': study.get('STUDY_TITLE', ''), 'MW.StudyDescription': study.get('STUDY_SUMMARY', ''), 'MW.StudyType': study.get('STUDY_TYPE', '')})
    mw_info = section('METABOLOMICS WORKBENCH')
    if mw_info:
        meta['MW.SubmissionDate'] = mw_info.get('CREATED_ON', '')
    subject = section('SUBJECT')
    if subject:
        meta.update({'MW.SubjectType': subject.get('SUBJECT_TYPE', ''), 'MW.Organisms': subject.get('SUBJECT_SPECIES', ''), 'MW.StudyType': subject.get('STUDY_TYPE', '')})
    ms = section('MS')
    if ms:
        meta.update({'MW.MSInstrumentName': ms.get('INSTRUMENT_NAME', ''), 'MW.MSInstrumentType': ms.get('INSTRUMENT_TYPE', ''), 'MW.MSType': ms.get('MS_TYPE', ''), 'MW.IonMode': ms.get('ION_MODE', '')})
    nmr = section('NMR')
    if nmr:
        meta.update({'MW.NMRInstrumentName': nmr.get('INSTRUMENT_NAME', ''), 'MW.NMRInstrumentType': nmr.get('INSTRUMENT_TYPE', ''), 'MW.MSType': nmr.get('MS_TYPE', ''), 'MW.IonMode': nmr.get('ION_MODE', '')})
    chromatography = section('CHROMATOGRAPHY')
    if chromatography:
        meta.update({'MW.ChromatographyType': chromatography.get('CHROMATOGRAPHY_TYPE', '')})
    meta['MW.StudyURL'] = f'https://www.metabolomicsworkbench.org/data/DRCCMetadata.php?Mode=Study&StudyID={study_id}'
    return meta

def fetch_gnps_ids(keywords: List[str]=('metabolomics', 'lipidomics', 'metabolome', 'lipidome'), timeout: float=DEFAULT_TIMEOUT) -> List[str]:
    """Return distinct MassIVE accessions whose keyword text contains a search term.

    Preserve response order. This optional helper is not called by the
    MTBLS-only workflow; network and JSON-reading errors can propagate.
    """
    ids: List[str] = []
    r = SESSION.get(GNPS_SEARCH_URL, timeout=timeout)
    data = r.json()
    for d in data.get('datasets', []):
        acc = str(d.get('dataset') or '').strip()
        kw = str(d.get('keywords') or '').lower()
        if GNPS_ID_PATTERN.match(acc) and any((k.lower() in kw.lower() for k in keywords)):
            ids.append(acc)
    seen, unique = (set(), [])
    for acc in ids:
        if acc not in seen:
            seen.add(acc)
            unique.append(acc)
    return unique

def get_gnps_metadata_bulk(msv_ids: List[str], timeout: float=DEFAULT_TIMEOUT) -> Dict[str, Dict[str, Any]]:
    """Read the MassIVE dataset list once and select the requested accessions.

    Build GNPS.* fields from the available records. Missing records retain only
    their accession and dataset link. This helper is retained for earlier
    multi-repository work, not called by the MTBLS-only collection workflow.
    """
    wanted = [s.strip() for s in msv_ids if isinstance(s, str) and GNPS_ID_PATTERN.match(s.strip())]
    if not wanted:
        return {}
    try:
        r = SESSION.get(GNPS_SEARCH_URL, timeout=timeout)
        data = r.json() if r.status_code == 200 else {}
    except ValueError:
        data = {}
    records = data.get('datasets') or data.get('results') or []
    idx = {}
    for rec in records:
        if not isinstance(rec, dict):
            continue
        acc = str(rec.get('dataset') or rec.get('accession') or rec.get('massive_id') or '').strip()
        if acc and GNPS_ID_PATTERN.match(acc):
            idx[acc] = rec

    def _join(v):
        """Join list values with semicolons, or return one nonempty value as text."""
        if isinstance(v, list):
            return '; '.join((str(x).strip() for x in v if str(x).strip()))
        return str(v).strip() if v else ''

    def _pick(*keys):
        """Read the first populated supported field in the current dataset record.

        For a list field, read the name from its first item. Return empty text
        when none of the requested fields is populated.
        """
        for k in keys:
            v = rec.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
            elif isinstance(v, list) and v:
                return v[0].get('name', '').strip()
        return ''
    out: Dict[str, Dict[str, Any]] = {}
    for msv in wanted:
        rec = idx.get(msv, {})
        meta = {'GNPS.Accession': msv, 'GNPS.StudyURL': GNPS_DATASET_URL_TMPL.format(msv=msv)}
        if rec:
            title = _pick('title', 'dataset_title', 'name')
            descr = _pick('description', 'dataset_description', 'short_description')
            pi = _pick('principal_investigator', 'pi', 'PI', 'lab_head')
            instruments = _pick('instrument', 'instruments')
            species = _pick('species', 'organism', 'organisms')
            keywords = _pick('keywords', 'tags', 'tag')
            modality = _pick('modality')
            if not modality:
                kw_str = _join(keywords).lower()
                if 'lipidomic' in kw_str and 'metabolomic' in kw_str:
                    modality = 'Lipidomics; Metabolomics'
                elif 'lipidomic' in kw_str:
                    modality = 'Lipidomics'
                elif 'metabolomic' in kw_str:
                    modality = 'Metabolomics'
                else:
                    modality = ''
            meta.update({'GNPS.Title': title, 'GNPS.Description': descr, 'GNPS.PrincipalInvestigator': pi, 'GNPS.Instruments': _join(instruments), 'GNPS.Organisms': _join(species), 'GNPS.Keywords': keywords, 'GNPS.Modality': modality})
        out[msv] = meta
    return out

def get_study_metadata(study_id: str | List[str]) -> Dict[str, Any]:
    """Retrieve metadata for one accession or a list of accessions.

    A single accession returns its metadata dictionary. A list returns a
    dictionary keyed by accession, using the matching repository helper.
    Unknown identifiers receive empty dictionaries. The main released
    workflow passes only MTBLS accessions.
    """
    print(f'\nFetching repository metadata for the supplied dataset identifiers...')
    if isinstance(study_id, list):
        ids = list(dict.fromkeys(study_id))
        mtbls_ids = [i for i in ids if MTBLS_ID_PATTERN.match(i)]
        mw_ids = [i for i in ids if MW_ID_PATTERN.match(i)]
        gnps_ids = [i for i in ids if GNPS_ID_PATTERN.match(i)]
        unknown = [i for i in ids if not (MTBLS_ID_PATTERN.match(i) or MW_ID_PATTERN.match(i) or GNPS_ID_PATTERN.match(i))]
        results: Dict[str, Dict[str, Any]] = {}
        for mtbls_id in track(mtbls_ids, description='MTBLS Metadata'):
            results[mtbls_id] = get_mtbls_metadata(mtbls_id)
            time.sleep(0.1)
        for mw_id in track(mw_ids, description='MW Metadata'):
            results[mw_id] = get_mw_metadata(mw_id)
            time.sleep(0.3)
        if gnps_ids:
            print('GNPS Metadata...', end='')
            results.update(get_gnps_metadata_bulk(gnps_ids))
            print('done')
        for uid in unknown:
            results.setdefault(uid, {})
        return results
    elif isinstance(study_id, str):
        if MTBLS_ID_PATTERN.match(study_id):
            return get_mtbls_metadata(study_id)
        elif MW_ID_PATTERN.match(study_id):
            return get_mw_metadata(study_id)
        elif GNPS_ID_PATTERN.match(study_id):
            return get_gnps_metadata_bulk([study_id]).get(study_id, {})
    print(f' done.')
    return {}

# Older Workbench example; it is not called by the MTBLS-only workflow.
if __name__ == '__main__':







    mw_ids = fetch_mw_ids()
    for mw_id in mw_ids:
        meta_mw = get_mw_metadata(mw_id)
        print(meta_mw)
    print('Metadata fetching complete.')
