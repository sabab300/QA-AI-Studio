"""Bounded public encyclopedia fallback; never substitutes fabricated context."""
import json
import re
from urllib.parse import urlparse

import requests

ENDPOINT = 'https://en.wikipedia.org/w/api.php'
MAX_RESPONSE_BYTES = 128 * 1024


def search_encyclopedia(question):
    query = re.sub(r'^(?:what|who)\s+(?:is|are|was|were)\s+(?:the\s+)?',
                   '', str(question or '').strip(), flags=re.I).rstrip(' ?')[:500]
    failure = {'success': False, 'context': '', 'references': [], 'provider': 'wikipedia'}
    if not query:
        return dict(failure, error='A search question is required.')
    params = {'action': 'query', 'generator': 'search', 'gsrsearch': query,
              'gsrlimit': 3, 'prop': 'extracts|info|pageprops', 'exintro': 1,
              'explaintext': 1, 'exchars': 2000, 'inprop': 'url', 'format': 'json'}
    try:
        with requests.get(ENDPOINT, params=params, timeout=15, stream=True,
                          headers={'User-Agent': 'QA-AI-Studio/1.0 (public knowledge search)'}) as response:
            if response.status_code != 200:
                return dict(failure, error=f'Encyclopedia search unavailable (HTTP {response.status_code}).')
            content = bytearray()
            for chunk in response.iter_content(chunk_size=4096):
                content.extend(chunk)
                if len(content) > MAX_RESPONSE_BYTES:
                    return dict(failure, error='Encyclopedia search response exceeded its safe size limit.')
            payload = json.loads(content)
        pages = payload.get('query', {}).get('pages', {})
        contents, references = [], []
        for page in sorted(pages.values(), key=lambda item: item.get('index', 999))[:3]:
            if 'disambiguation' in page.get('pageprops', {}):
                continue
            title = str(page.get('title') or '')[:200]
            extract = str(page.get('extract') or '').strip()[:2000]
            url = str(page.get('fullurl') or '')
            parsed = urlparse(url)
            if not extract or parsed.scheme != 'https' or parsed.hostname != 'en.wikipedia.org':
                continue
            contents.append(title + '\n' + extract)
            references.append(url)
        if not contents:
            return dict(failure, error='No usable encyclopedia results found.')
        return {'success': True, 'context': '\n\n'.join(contents),
                'references': references, 'provider': 'wikipedia',
                'warnings': ['DuckDuckGo search unavailable; Wikipedia encyclopedia fallback used. Coverage is not comprehensive web search.']}
    except (requests.RequestException, ValueError, TypeError, AttributeError):
        return dict(failure, error='Encyclopedia search failed or returned invalid data.')
