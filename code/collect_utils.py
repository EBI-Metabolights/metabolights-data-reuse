"""Shared helpers for reading configuration and publication details.

These functions read local settings, find DOIs and years in text, join author
names, flatten nested metadata and remove HTML tags. They do not decide
whether a publication reused data.
"""

import json
import re
from typing import Mapping
import html as _html

DOI_RE = re.compile(r'10\.\d{4,9}/[-._;()/:A-Z0-9]+', re.IGNORECASE)

def _load_config():
    """Read configuration from the current working directory.

    Try config.json first, then the older config_default.json name. Return an
    empty dictionary if neither file exists. Invalid JSON is reported by the
    JSON reader rather than silently replaced. Run collection from code/ when
    the private config.json file is stored there.
    """
    for fname in ["config.json", "config_default.json"]:
        try:
            with open(fname) as f:
                return json.load(f)
        except FileNotFoundError:
            continue
    return {}

def _extract_doi_from_text(*texts: str) -> str:
    """Return the first DOI found in the supplied texts, or an empty string."""
    for t in texts:
        if not isinstance(t, str):
            continue
        m = DOI_RE.search(t)
        if m:
            return m.group(0)
    return ''

def _extract_year(*dates: str) -> str:
    """Return the first four-digit year from 1900 to 2099 in the supplied texts.

    Texts are checked in argument order, so callers determine which date field
    takes priority. Return an empty string when no year is found.
    """
    for d in dates:
        if not isinstance(d, str):
            continue
        m = re.search(r'\b(19|20)\d{2}\b', d)
        if m:
            return m.group(0)
    return ''

def _authors_joined(entry) -> str:
    """Read supported author fields and join names with semicolons.

    Accept the list, dictionary and text formats used by the literature sources.
    Use the first supported field that contains names; return an empty string
    when no supported author field is populated.
    """
    get = entry.get if hasattr(entry, 'get') else lambda *_: None
    authors = get('authors')
    if isinstance(authors, list):
        names = []
        for a in authors:
            if isinstance(a, dict):
                nm = a.get('name') or a.get('term') or a.get('$')
                if nm:
                    names.append(str(nm).strip())
            elif isinstance(a, str):
                if a.strip():
                    names.append(a.strip())
        if names:
            return '; '.join(names)

    elif isinstance(authors, dict):
        names = []
        if isinstance(authors.get('author', []), list):
            for a in authors.get('author', []):
                if isinstance(a, dict):
                    nm = a.get('name') or a.get('term') or a.get('$')
                    if nm:
                        names.append(str(nm).strip())
                elif isinstance(a, str):
                    if a.strip():
                        names.append(a.strip())
            if names:
                return '; '.join(names)

    for key in ('creators', 'authors', 'author-list', 'authors.author'):
        val = get(key)
        if isinstance(val, list):
            names = []
            for v in val:
                if isinstance(v, dict):
                    nm = v.get('name') or v.get('$') or v.get('ce:indexed-name') or v.get('surname') or v.get('ce:surname') or v.get('ce:initials')
                    if nm:
                        names.append(str(nm).strip())
                elif isinstance(v, str) and v.strip():
                    names.append(v.strip())
            if names:
                return '; '.join(names)

    a = get('author') or get('dc_creator') or get('dc:creator')
    if isinstance(a, str) and a.strip():
        return a.replace('\n', ' ').strip()

    return ''

def _flatten_dict(d: dict, parent_key: str = '', sep: str = '.') -> dict:
    """Convert nested metadata to one dictionary with paths as keys.

    For example, a nested key can become study.title. Lists of simple values
    are joined with semicolons; dictionary entries inside lists receive numeric
    positions in their key paths.
    """
    items = []
    if isinstance(d, list):
        d = {str(i): v for i, v in enumerate(d)}
    for k, v in d.items():
        new_key = f'{parent_key}{sep}{k}' if parent_key else str(k)
        if isinstance(v, Mapping):
            items.extend(_flatten_dict(v, new_key, sep=sep).items())
        elif isinstance(v, list):
            if all((not isinstance(x, Mapping) for x in v)):
                items.append((new_key, '; '.join([str(x) for x in v])))
            else:
                items.extend(_flatten_dict({str(i): x for i, x in enumerate(v)}, new_key, sep=sep).items())
        else:
            items.append((new_key, v))
    return dict(items)

def _compact_join(values) -> str:
    """Sort distinct nonempty text or numeric values and join them with semicolons."""
    return '; '.join(sorted({str(v).strip() for v in values if isinstance(v, (str, int, float)) and str(v).strip()}))

def _strip_html(text: str) -> str:
    """Remove HTML tags, reduce repeated whitespace and decode HTML character codes."""
    if not isinstance(text, str):
        return ''
    text = re.sub('<[^>]+>', ' ', text)
    return _html.unescape(re.sub(r'\s+', ' ', text)).strip()
