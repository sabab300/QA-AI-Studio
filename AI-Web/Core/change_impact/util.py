"""Small deterministic helpers shared by the Change Impact modules."""
import hashlib
import json
import re
from datetime import datetime, timezone

STOPWORDS = frozenset((
    'the a an and or of to in on for with by is are be shall must should will can may from that this these those it its as at '
    'not no any all each per if then than when where which who whom into onto over under between within without').split())


class ImpactError(Exception):
    """Controlled failure with an HTTP-style status and stable code."""

    def __init__(self, message, status=409, code='IMPACT_ERROR'):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    if isinstance(value, bytes):
        return hashlib.sha256(value).hexdigest()
    if not isinstance(value, str):
        value = json.dumps(value, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def stable_id(prefix, *parts, length=10):
    return '%s-%s' % (prefix, digest('|'.join(str(p) for p in parts))[:length].upper())


def normalize(text):
    """Lower-case, collapse whitespace, drop list numbering and punctuation noise."""
    text = re.sub(r'\s+', ' ', str(text or '')).strip().lower()
    return re.sub(r'[^\w\s/.\-_]', '', text)


def tokens(text):
    return [t for t in re.findall(r"[a-z0-9_][a-z0-9_.\-/]*", str(text or '').lower())
            if t not in STOPWORDS and len(t) > 1]


def token_set(text):
    return set(tokens(text))


def jaccard(a, b):
    a, b = set(a), set(b)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def containment(small, big):
    """Share of ``small`` tokens present in ``big`` (0..1)."""
    small, big = set(small), set(big)
    if not small:
        return 0.0
    return len(small & big) / len(small)


def clip(text, limit=400):
    text = str(text or '')
    return text if len(text) <= limit else text[:limit - 1].rstrip() + '…'


def loads(value, default=None):
    if value in (None, ''):
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def dumps(value):
    return json.dumps(value, sort_keys=True, default=str, ensure_ascii=False)
