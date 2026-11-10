"""Term extraction for dependency evidence.

Document similarity is not functional dependency. A dependency needs something *specific* that two statements share:
a requirement reference, a named business object ("Release Order"), an API path, a field or an identifier. Ordinary words
are only supporting evidence. This module separates the two.
"""
import re

from Core.change_impact.util import STOPWORDS, normalize

REF = re.compile(r'\b(?:REQ|FR|NFR|BR|CR|SR|UC|API|SYS|DR|BRD|FRS)[-_ ]?\d+[A-Za-z0-9._\-]*\b', re.I)
DOC_NUMBER = re.compile(r'\b[A-Z]{2,6}-[A-Z]{2,}(?:-[A-Z0-9]{2,})*-\d{2,}(?:-[A-Z0-9]+)*\b')
IDENT = re.compile(r'\b[A-Za-z]{2,}(?:_[A-Za-z0-9]+)+\b|\b[a-z]{2,}[A-Z][A-Za-z0-9]+\b|(?<![\w/])/[A-Za-z0-9_\-{}.]+(?:/[A-Za-z0-9_\-{}.]+)+')
ACRONYM = re.compile(r'\b[A-Z][A-Z0-9]{2,}\b')
NAMED = re.compile(r"\b[A-Z][a-z]+(?:(?:\s+(?:of|and|for|in|to)\s+|\s+)[A-Z][a-z]+){1,3}\b")
QUOTED = re.compile(r"[\"'‘“]([^\"'’”]{3,60})[\"'’”]")

# capitalised words that start sentences or captions and do not name a business object
LEAD_NOISE = frozenset((
    'the this that these those there then when where which while with without if in on for to of and or as at by from into a an it its '
    'requirement requirements figure screenshot table image section note example however also once each every all any following such '
    'priority description detail details change request page document version').split())
# acronyms that appear in nearly every document and carry no meaning on their own
ACRONYM_NOISE = frozenset(('THE', 'AND', 'FOR', 'NOT', 'ALL', 'CRF', 'SRS', 'FRS', 'BRD', 'PDF', 'DOC', 'NIL', 'YES', 'REQ', 'API', 'SQL', 'URL', 'HTTP',
                           'HTTPS', 'JSON', 'XML', 'UAT', 'N/A', 'TBD', 'FRD', 'NFR', 'POST', 'GET', 'PUT', 'PATCH', 'DELETE', 'JSON', 'XML', 'HTML', 'CSV', 'XLSX', 'OK'))
INTEGRATION_WORDS = frozenset('api endpoint integration gateway esb webhook token payload queue swagger rest soap sftp'.split())      # 'interface' and 'message' are screen words ("on the Review and Validate interface", "show a validation message")
RULE_WORDS = frozenset('must shall limit maximum minimum mandatory optional allowed validate validation rule threshold duration quantity'.split())


def refs(text):
    return {re.sub(r'[\s_]+', '-', m.group(0).upper()) for m in REF.finditer(str(text or ''))}


CRF_NAME = re.compile(r'(?<![\w-])CRF[-_][A-Za-z0-9]+(?:[-_][A-Za-z0-9]+){1,12}(?![\w-])', re.I)


def document_numbers(text):
    """CRF / document identifiers such as CRF-DPP-0008-29052023 or CRF-DEMO-PSW-Core-Sindh-Excise-PITB-Integration, used to see that another
    document names this change request."""
    text = str(text or '')
    found = {m.group(0).upper() for m in DOC_NUMBER.finditer(text) if any(ch.isdigit() for ch in m.group(0))}
    found |= {re.sub(r'_', '-', m.group(0)).upper() for m in CRF_NAME.finditer(text)}
    return found


def _clean_named(phrase):
    words = phrase.split()
    while words and words[0].lower() in LEAD_NOISE:
        words.pop(0)
    while words and words[-1].lower() in LEAD_NOISE:
        words.pop()
    if len(words) < 2:
        return ''
    return ' '.join(words)


def entities(text):
    """{normalised term: kind}. Kinds: ident, named, acronym, quoted. Order of precedence keeps the most specific kind."""
    text = str(text or '')
    found = {}
    for m in QUOTED.finditer(text):
        value = normalize(m.group(1))
        if len(value) >= 4 and len(value.split()) <= 6 and not all(w in STOPWORDS for w in value.split()):
            found.setdefault(value, 'quoted')
    for m in NAMED.finditer(text):
        value = _clean_named(m.group(0))
        if value:
            found.setdefault(normalize(value), 'named')
    for m in ACRONYM.finditer(text):
        if m.group(0) not in ACRONYM_NOISE and not m.group(0).isdigit():
            found.setdefault(m.group(0).lower(), 'acronym')
    for m in IDENT.finditer(text):
        value = m.group(0).rstrip('.,;:-')      # a sentence-ending full stop is not part of a path or identifier
        if value:
            found[normalize(value)] = 'ident'
    return found


def keywords(text, extra_stop=frozenset()):
    """Plain content words (supporting evidence only)."""
    words = re.findall(r'[a-z][a-z0-9_]{3,}', str(text or '').lower())
    return {w for w in words if w not in STOPWORDS and w not in extra_stop}


def tags(text):
    words = set(re.findall(r'[a-z]+', str(text or '').lower()))
    out = []
    if words & INTEGRATION_WORDS:
        out.append('Integration')
    if words & RULE_WORDS:
        out.append('Business rule / validation')
    return out


def sentences(text):
    """Verbatim sentences / lines of a chunk (so a quote can always be found again in the chunk)."""
    parts = []
    for line in str(text or '').splitlines():
        line = line.strip()
        if not line:
            continue
        for piece in re.split(r'(?<=[.!?])\s+(?=[A-Z0-9\[(])', line):
            piece = piece.strip()
            if len(piece) >= 12:
                parts.append(piece)
    return parts


def quote_in(quote, text):
    """True when ``quote`` occurs in ``text`` ignoring whitespace and case differences."""
    a = re.sub(r'\s+', ' ', str(quote or '')).strip().lower()
    b = re.sub(r'\s+', ' ', str(text or '')).strip().lower()
    return bool(a) and a in b


# ======================================================================================================================
# Engine 2.0: requirement units, hard keys and context words
#
# A "hard key" is something a requirement names precisely: a requirement id, an API path, a header name, a field name, a rule
# value with its unit, a named business object. A hard key is EVIDENCE, never proof on its own: two statements that share one
# must also talk about the same thing (context words) before a dependency is Confirmed. Platform vocabulary (PSW, WeBOC, ESB,
# PITB, CESS ...) names the whole system, so it is never a key and never a context word.
# ======================================================================================================================
STRONG, MEDIUM, WEAK = 'strong', 'medium', 'weak'
PLATFORM = frozenset('psw weboc esb pitb cess fbr oga'.split())
PLATFORM_PHRASES = re.compile(r'\bpakistan single window\b|\bsingle window\b', re.I)
# labels printed by the change-request form itself; they are the same in every CRF and prove nothing about a requirement
TEMPLATE_PHRASES = re.compile(
    r'impact of not implementing(?: the| proposed| the requested| the proposed)? change|justification(?: for requesting the change)?|legal references?(?: \(if applicable\))?|'
    r'alternatives? to the proposed change|whether or not the requested change shall require a change in|change request (?:form|number|id)|'
    r'requested change(?: description)?|proposed change|description of problem(?: ?/ ?issue)?|change description', re.I)
# words that carry no functional meaning in a requirement (modal verbs, document and QA boilerplate)
GENERIC_WORDS = frozenset((
    'shall must should will would could may might can such system user users page screen field fields value values data test tests case cases step steps result results '
    'expected actual verify validate ensure check checks include includes included following given using used use also only when then else new existing current '
    'based per via etc able need needs required requires require provide provided provides allow allowed allows display displayed displays show shows shown '
    'should shall status information details detail record records list lists item items type types name names number numbers description text message messages '
    'application applications module modules function functions functionality feature features process processes service services request requests response responses '
    'click button open save submit enter select every critical high low medium priority mandatory optional root string integer boolean decimal yes none null').split() + ['post', 'put', 'patch', 'send', 'sent', 'sends'])

HEADER_NAME = re.compile(r'(?<![\w-])[Xx]-[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+){0,3}(?![\w-])')
URL = re.compile(r'https?://[^\s`\'"<>)\]]+', re.I)
ENV_SEGMENT = re.compile(r'^/(?:sit|qa|uat|dev|test|stg|stage|staging|prod|production|live)(?=/)', re.I)


def url_paths(text):
    """API paths written inside full URLs: https://sit.psw.gov.pk/qa/esb/api/report -> /esb/api/report (host and environment segment are not part of the API)."""
    out = []
    for m in URL.finditer(str(text or '')):
        rest = re.sub(r'^https?://[^/\s]+', '', m.group(0), flags=re.I).split('?', 1)[0].split('#', 1)[0].rstrip('.,;:-)')
        path = ENV_SEGMENT.sub('', rest)
        if path.count('/') >= 2 and re.search(r'[A-Za-z]', path):
            out.append((path.lower(), path))
    return out


ENDPOINT = re.compile(r'(?<![\w/])/[A-Za-z0-9_\-{}.]+(?:/[A-Za-z0-9_\-{}.]+)+')
NAMESPACED_ID = re.compile(r'(?<![\w-])[A-Z][A-Z0-9]{1,7}(?:[-_][A-Z][A-Z0-9]{1,7})+[-_]\d{1,5}[A-Za-z]?(?![\w-])')
PLAIN_ID = re.compile(r'(?<![\w-])(?:REQ|FR|NFR|BR|CR|SR|UC|API|SYS|DR|BRD|FRS|SQL)[-_ ]?\d{1,5}[A-Za-z]?(?![\w-])', re.I)
FIELD_NAME = re.compile(r'(?<![\w/])[A-Za-z]{2,}(?:_[A-Za-z0-9]+)+(?![\w-])|(?<![\w/])[a-z]{2,}[A-Z][A-Za-z0-9]*(?![\w-])')
RULE_VALUE = re.compile(r'(?<![\w.])(\d+(?:[.,]\d+)?)\s*[-‐-–]?\s*(days?|weeks?|months?|years?|hours?|minutes?|seconds?|characters?|chars?|digits?|kb|mb|gb|records?|attempts?|rows?|times)\b', re.I)
NOT_ID_PREFIX = frozenset(('TC', 'CRF', 'ISO', 'RFC', 'UTF', 'SHA', 'HTTP', 'HTTPS', 'ID', 'NO', 'REV', 'TBL', 'FIG'))
UNIT_SINGULAR = {'chars': 'character', 'char': 'character', 'times': 'time'}

QA_INSTRUCTION = re.compile(
    r'\bQA AI Studio\b|\bgenerate (?:the )?test cases?\b|\btest cases? (?:must|should|shall|need|will)\b|\bTC Number\b|\bQA (?:engineer|team|analyst)s?\b|'
    r'\b(?:api|ui|sql|playwright) (?:test )?cases? (?:must|should|shall)\b|\b(?:used|run|executed|performed) in (?:live )?(?:testing|uat)\b|\blive testing\b|'
    r'\btest (?:data|steps|scenarios?) (?:must|should|shall)\b|\bwrite (?:the )?test cases?\b|\bexpected results? (?:must|should|shall)\b', re.I)
DOCUMENT_HEADER = re.compile(
    r'^\s*(?:document (?:control|title|id|number|version|history|name|type|owner)|version history|revision history|change history|prepared by|reviewed by|approved by|'
    r'table of contents|contents|page \d+(?: of \d+)?|confidential|copyright|change request(?: form)?|crf (?:no|number|id|title|date)|author|distribution list|'
    r'document status|record of changes|sign[- ]?off|references?|abbreviations?|glossary|purpose|scope|introduction|overview|background|assumptions?|revision|date|version|status)\b\s*[:|\-]?',
    re.I)
DANGLING = frozenset('and or the of to for with by in on at a an as but if that which is are be nor'.split())
HEADER_VERBS = re.compile(r'\b(shall|must|should|will|cannot|may|prevent|reject|allow|validate|link|route|generate|accept)\b', re.I)
MODAL_WORDS = re.compile(r'\b(shall|must|should|will|required|needs? to|has to|is to|cannot|can|may|must not|prevent|reject|allow|validate|link|route|generate|accept|include|display)\b', re.I)


def stem(word):
    w = str(word).lower()
    for suffix in ('ations', 'ation', 'ating', 'ated', 'ates', 'ings', 'ing', 'ies', 'ied', 'ers', 'ate', 'ure', 'ed', 'es', 'ly', 'er', 's'):
        if w.endswith(suffix) and len(w) - len(suffix) >= 3:
            base = w[:-len(suffix)]
            return base + 'y' if suffix in ('ies', 'ied') else base
    return w[:-1] if w.endswith('e') and len(w) > 4 else w


def norm_id(value):
    return re.sub(r'[\s_]+', '-', str(value).upper())


def _is_namespaced(value):
    return value.count('-') >= 2


DATA_TYPES = frozenset('string integer int number boolean bool array object date datetime timestamp bigint varchar float double decimal null true false uuid guid'.split())
JSON_LINE = re.compile(r'^\s*[\{\[,]?\s*"[\w.\-]+"\s*:\s*[^|]{0,80}$')


def json_fragment(text):
    """A line of a sample request/response body (\"hsCode\": \"string\",): data, not a requirement."""
    t = re.sub(r'\s+', ' ', str(text or '')).strip()
    return bool(JSON_LINE.match(t)) and len(t.split()) <= 6


def form_field_row(text):
    """A row of the change-request form itself ("Impact of Not Implementing the requested change | ...", or a label whose
    value was left blank): form wording, not a requirement."""
    t = re.sub(r'^[\s|]+|[\s|]+$', '', str(text or ''))
    if ' | ' in t:
        lead = t.split(' | ', 1)[0].strip().rstrip(':').strip()
        return bool(TEMPLATE_PHRASES.fullmatch(lead))
    if ':' in t and TEMPLATE_PHRASES.fullmatch(t.split(':', 1)[0].strip()):           # "Justification: This change will ensure ..."
        return True
    label = re.sub(r'\s*\([^)]*\)\s*$', '', t).rstrip(':').strip()      # "Legal references (if applicable)" and the like
    return bool(TEMPLATE_PHRASES.fullmatch(label)) or bool(re.match(r'whether or not the requested change shall require\b', t, re.I))


def hard_keys(text, same_knowledge=False):
    """{key: {'kind','strength','surface'}} for one statement. The key is the normalised form used to compare two statements."""
    text = CRF_NAME.sub(' ', TEMPLATE_PHRASES.sub(' ', PLATFORM_PHRASES.sub(' ', str(text or ''))))      # a change request's own name is not a requirement key
    found = {}
    url_keys = url_paths(text)
    text = URL.sub(' ', text)                                                  # host names and URL fragments are not words or identifiers

    def put(key, kind, strength, surface):
        if not key:
            return
        current = found.get(key)
        order = {STRONG: 3, MEDIUM: 2, WEAK: 1}
        if current is None or order[strength] > order[current['strength']]:
            found[key] = {'kind': kind, 'strength': strength, 'surface': surface}

    for key, surface in url_keys:
        put(key, 'api path', STRONG, surface)
    covered = []
    for m in NAMESPACED_ID.finditer(text):
        ident = norm_id(m.group(0))
        if ident.split('-')[0] in NOT_ID_PREFIX:
            continue
        covered.append((m.start(), m.end()))
        put(ident, 'requirement id', STRONG, m.group(0))
    for m in PLAIN_ID.finditer(text):
        if any(a <= m.start() < b for a, b in covered):
            continue
        ident = norm_id(m.group(0))
        # a bare "REQ-102" is numbered per document, so the same id in unrelated documents is a coincidence unless the Knowledge is the same
        put(ident, 'requirement id', STRONG if same_knowledge else MEDIUM, m.group(0))
    for m in HEADER_NAME.finditer(text):
        put(m.group(0).lower(), 'header', STRONG, m.group(0))
    for m in ENDPOINT.finditer(text):
        value = m.group(0).rstrip('.,;:-')
        if re.search(r'[A-Za-z]', value) and not re.fullmatch(r'/\d+(?:/\d+)+', value):
            put(value.lower(), 'api path', STRONG, value)
    for m in FIELD_NAME.finditer(text):
        value = m.group(0)
        if value.split('_')[0].upper() in NOT_ID_PREFIX or value.lower() in PLATFORM:
            continue
        put(value.lower(), 'field', MEDIUM, value)
    for m in RULE_VALUE.finditer(text):
        unit = m.group(2).lower().rstrip('s') if m.group(2).lower() not in ('kb', 'mb', 'gb') else m.group(2).lower()
        unit = UNIT_SINGULAR.get(unit, unit)
        number = m.group(1).replace(',', '.').rstrip('0').rstrip('.') if '.' in m.group(1) else m.group(1)
        put('rule:%s %s' % (number, unit), 'rule value', MEDIUM, m.group(0))
    for m in QUOTED.finditer(text):
        value = normalize(m.group(1))
        if len(value) >= 4 and len(value.split()) <= 6 and not all(w in STOPWORDS for w in value.split()) and value not in DATA_TYPES:
            put(value, 'quoted name', MEDIUM, m.group(1))
    for m in NAMED.finditer(text):
        value = _clean_named(m.group(0))
        words = value.lower().split()
        if value and not all(w in PLATFORM or w in GENERIC_WORDS for w in words):
            put(normalize(value), 'business object', MEDIUM, value)
    for m in ACRONYM.finditer(text):
        value = m.group(0)
        if value not in ACRONYM_NOISE and value.lower() not in PLATFORM and not value.isdigit() and not value.startswith(('HTTP',)):
            put(value.lower(), 'acronym', WEAK, value)
    return found


def context_words(text, keys=None):
    """Stemmed content words of a statement, without the words that belong to its own hard keys, platform terms and boilerplate."""
    text = URL.sub(' ', CRF_NAME.sub(' ', TEMPLATE_PHRASES.sub(' ', PLATFORM_PHRASES.sub(' ', str(text or '')))))
    for info in (keys or {}).values():
        if info['surface']:
            text = text.replace(info['surface'], ' ')
    text = re.sub(r'[A-Za-z]+_[A-Za-z0-9_]+|\b[a-z]+[A-Z][A-Za-z0-9]*\b', ' ', text)      # field names are keys, not words
    words = set()
    for raw in re.findall(r'[A-Za-z][A-Za-z]{2,}', text):
        low = raw.lower()
        if low in STOPWORDS or low in PLATFORM or low in GENERIC_WORDS:
            continue
        words.add(stem(low))
    return words


def qa_instruction(text):
    return bool(QA_INSTRUCTION.search(str(text or '')))


def header_row(text):
    """A table header such as "API | Method | QA Endpoint | Required Headers | Routing/Rule": short cells, no sentence, no value."""
    cells = [c.strip() for c in str(text or '').split('|')]
    if len(cells) < 3 or any(not c for c in cells):
        return False
    if any(len(c.split()) > 4 or re.search(r'\d|[.!?]$', c) for c in cells):
        return False
    return not any(HEADER_VERBS.search(c) for c in cells)


def fragment(text):
    """Incomplete text: too short to state anything, a dangling clause, or a bare label."""
    t = re.sub(r'\s+', ' ', str(text or '')).strip()
    if not t:
        return True
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'\-_/]*", t)
    if len(words) < 4 and not hard_keys(t) and not (' | ' in t and len(words) >= 2):
        return True
    if t.endswith(':') and len(words) < 8:          # a long sentence that introduces a list ("...amend the following data elements:") is a requirement, not a label
        return True
    if t.rstrip('.').split(' ')[-1].lower() in DANGLING:
        return True
    if t[0].islower() and len(words) < 6 and not hard_keys(t):
        return True
    return False


def document_header(text):
    t = re.sub(r'^[\s|]+|[\s|]+$', '', str(text or ''))      # a running page header arrives as "| Change Request Form - CRF |"
    return bool(DOCUMENT_HEADER.match(t)) and len(t.split()) <= 14 and not MODAL_WORDS.search(t)


ADMIN_CLOSING = re.compile(
    r'^\s*(?:once|after|when)\s+(?:the\s+)?(?:stated|above|mentioned|requested|following|listed)\s+(?:requirements?|changes?|change requests?)\s+(?:have|has|are|is)\s+been\s+'
    r'(?:implemented|completed|deployed|developed|approved)|\bchange requirements?\s+requested\s+herein\b|^\s*(?:thank you|kindly|please\s+(?:find|review|approve|consider))\b', re.I)


REQUIREMENT_TITLE = re.compile(r'^\s*(?:requirement|req\.?)\s*\d+(?:\.\d+)*\s*[:.\-]', re.I)
LEAD_IN = re.compile(r'^\s*(?:the\s+)?following\s+(?:changes?|requirements?|points?|items?|modifications?)\s+(?:are|is)\s+(?:required|needed|listed|given|proposed)\b[^|]{0,40}$', re.I)


def lead_in(text):
    """A sentence that only announces what follows ("The following changes are required in the system."): it states no requirement itself."""
    return bool(LEAD_IN.match(str(text or '')))


def administrative_closing(text):
    """The closing or outcome wording of a change-request form ("Once the stated requirements have been implemented, ..."): administrative, not a product requirement."""
    return bool(ADMIN_CLOSING.search(str(text or '')))


def noise_kind(text):
    """'' when the statement is a real requirement, otherwise why it is set aside."""
    if administrative_closing(text):
        return 'Administrative closing statement of the change request (not a product requirement)'
    if form_field_row(text):
        return 'Change-request form field (not a requirement)'
    if REQUIREMENT_TITLE.match(str(text or '')) and not MODAL_WORDS.search(str(text or '')):
        return 'Requirement heading (a title, states no rule)'
    if lead_in(text):
        return 'Introductory sentence (announces the requirements that follow, states none)'
    if qa_instruction(text):
        return 'QA-authoring instruction (about writing tests, not a product requirement)'
    if header_row(text):
        return 'Table header row'
    if document_header(text):
        return 'Document header or administrative text'
    if fragment(text):
        return 'Incomplete fragment'
    return ''


def requirement_units(text, limit=700):
    """Requirement-level statements of a text block: one per line or table row (a row keeps its id), long lines split by sentence.
    Returns [{'text','ref'}] (verbatim substrings); noise lines are left out."""
    out = []
    for line in str(text or '').splitlines():
        line = line.strip()
        if not line or noise_kind(line):
            continue
        ids = [m.group(0) for m in NAMESPACED_ID.finditer(line) if norm_id(m.group(0)).split('-')[0] not in NOT_ID_PREFIX] or [m.group(0) for m in PLAIN_ID.finditer(line)]
        ref = norm_id(ids[0]) if ids else ''
        if len(line) <= limit:
            out.append({'text': line, 'ref': ref})
            continue
        for piece in sentences(line):
            if not noise_kind(piece):
                out.append({'text': piece, 'ref': ref})
    return out


def background_words(unit_texts, share=0.3, minimum_units=10):
    """Words that occur in many statements of one document ("report", "routing" in an ESB specification): they describe the document, not a requirement."""
    if len(unit_texts) < minimum_units:
        return frozenset()
    count = {}
    for t in unit_texts:
        for w in context_words(t):
            count[w] = count.get(w, 0) + 1
    limit = max(3, int(len(unit_texts) * share))
    return frozenset(w for w, n in count.items() if n >= limit)
