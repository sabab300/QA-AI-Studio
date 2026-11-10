"""Source document loading and clause segmentation (read-only)."""
import bisect
import re
from pathlib import Path

from Core.change_impact.util import ImpactError, digest, normalize, tokens

REF_PATTERN = re.compile(
    r'^\s*[\[(]?\s*((?:REQ|FR|NFR|BR|CR|CRF|SR|UC|API|SYS|DR|BRD|FRS)[-_ ]?\d+[A-Za-z0-9._\-]*)\s*[\])]?', re.I)
INLINE_REF = re.compile(r'\b((?:REQ|FR|NFR|BR|CR|CRF|SR|UC|API|SYS|DR)[-_ ]?\d+[A-Za-z0-9._\-]*)\b', re.I)
NUMBERED = re.compile(r'^\s*(\d+(?:\.\d+){0,5})[.)]?\s+(\S.*)$')
# "Requirement 4:" style labels. They name the block that follows; they are not requirements themselves.
LABEL_HEADING = re.compile(r'^\s*((?:requirement|req|rule|scenario|use case|story|feature|change)\s*#?\s*\d+[A-Za-z]?)\s*[:.\-]?\s*$', re.I)
# "Requirement 1: Submission of ..." (a label followed by its title), also after a field label such as
# "Specific Requirements Definition: Requirement 1: ...". The label becomes the context of the clauses that follow;
# the title is a heading, not a requirement, unless it states behaviour (modal verb).
LABEL_WITH_TITLE = re.compile(r'^\s*(?:[A-Za-z][A-Za-z /&\-]{2,60}:\s*)?((?:requirement|req|rule|scenario|use case|story|feature)\s*#?\s*\d+[A-Za-z]?)\s*[:\-\u2013]\s*(\S.{0,240})$', re.I)
MODAL = re.compile(r'\b(shall|must|should|will|required|needs? to|has to|is to|can|may)\b', re.I)


def normalize_ref(ref):
    return re.sub(r'[\s_]+', '-', str(ref or '').strip().upper())


class SourceDocumentLoader:
    """Loads one Knowledge Hub source through the canonical hierarchy and managed repository."""

    def __init__(self, db, central=None, text_reader=None, limits=None):
        self.db = db
        self.central = central
        self.limits = limits or {}
        self._text_reader = text_reader

    def _read_text(self, path):
        """Return (text, marks). ``marks`` locate every block (table, row, cell) for source references."""
        if self._text_reader:
            return self._text_reader(path), []
        if str(path).lower().endswith('.docx'):
            try:
                text, marks = read_docx_structured(path)
                if text.strip():
                    return text, marks
            except Exception:
                pass   # unusual file: fall back to the shared extractor below
        from Core.text_extractor import TextExtractor
        return TextExtractor().extract_text(str(path)), []

    def load(self, source_id):
        import sqlite3
        conn = self.db.get_connection()
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute('SELECT * FROM knowledge_items WHERE id=?', (int(source_id),)).fetchone()
            if not row:
                raise ImpactError('Source document %s was not found.' % source_id, 404, 'SOURCE_NOT_FOUND')
            row = dict(row)
            owner = None
            if row.get('document_version_id'):
                from Shared.document_hierarchy import DocumentHierarchy
                conn.row_factory = None
                try:
                    owner = DocumentHierarchy(conn).context(row['document_version_id'])
                finally:
                    conn.row_factory = sqlite3.Row
        finally:
            conn.close()
        if not owner:
            raise ImpactError('Resolve this document\'s Domain/Module/Knowledge/Document Type/Version owner in Knowledge Hub before analysis.',
                              409, 'SOURCE_OWNER_UNRESOLVED')
        if str(row.get('status') or '').strip().lower() in ('deleted', 'retired', 'archived'):
            raise ImpactError('This source document is not active.', 409, 'SOURCE_INACTIVE')
        path = self._resolve(row)
        max_bytes = int(self.limits.get('max_source_bytes', 32 * 1024 * 1024))
        if not path.is_file():
            raise ImpactError('The stored source file is unavailable. Restore it in Knowledge Hub and retry.', 409, 'SOURCE_FILE_MISSING')
        if path.stat().st_size > max_bytes:
            raise ImpactError('The source file is larger than the %d MiB analysis limit.' % (max_bytes // (1024 * 1024)), 400, 'SOURCE_TOO_LARGE')
        try:
            text, marks = self._read_text(path)
        except Exception as error:  # extraction libraries raise many types; never leak a stack trace
            raise ImpactError('The source text could not be extracted (%s).' % type(error).__name__, 400, 'SOURCE_UNREADABLE') from error
        if not str(text or '').strip():
            raise ImpactError('The source contains no extractable text (a scanned image PDF needs OCR first).', 400, 'SOURCE_EMPTY')
        limit = int(self.limits.get('max_source_characters', 400000))
        truncated = len(text) > limit
        text = text[:limit]
        return {
            'source_id': row['id'], 'file_name': row.get('file_name') or row.get('knowledge_name') or ('source-%s' % row['id']),
            'sha256': row.get('sha256') or digest(text), 'text': text, 'characters': len(text), 'truncated': truncated,
            'document_version_id': owner['document_version_id'],
            'owner': {k: owner[k] for k in ('domain', 'module', 'knowledge_name', 'document_type', 'version')},
            'owner_ids': {k: owner[k] for k in ('domain_id', 'module_id', 'knowledge_entity_id', 'document_type_id', 'document_version_id')},
            'clauses': segment(text, int(self.limits.get('max_clauses_per_document', 4000)), marks),
        }

    def _resolve(self, row):
        if self.central is None:
            return Path(str(row.get('repository_path') or ''))
        path = self.central.resolve_managed(row.get('repository_path'), row.get('repository_relative_path') or '')
        root = Path(self.central.root).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            raise ImpactError('The stored path is outside the managed repository and was refused.', 403, 'SOURCE_PATH_REFUSED') from None
        return path


def read_docx_structured(path):
    """Read a .docx in document order with merged cells counted once and nested tables included.

    The shared extractor lists a merged table cell once per grid column it spans, so one requirement block could appear
    several times and look like several independent changes. This reader walks the real cell elements instead.
    Returns (text, marks); every mark is {'offset', 'location', 'label'} for the block that starts at that offset.
    """
    from docx import Document
    from docx.oxml.ns import qn
    document = Document(str(path))
    blocks = []   # (text, location, label)
    counter = {'table': 0}   # numbers top-level tables only, so a nested table never shifts the numbering

    def para_text(p):
        return ''.join(node.text or '' for node in p.iter(qn('w:t'))).replace('\xa0', ' ').strip()

    def cell_lines(tc, location):
        lines = []
        for child in tc.iterchildren():
            if child.tag == qn('w:p'):
                lines.append(para_text(child))
            elif child.tag == qn('w:tbl'):
                lines.append('')
                for text, loc, label in table_blocks(child, location + ' > nested', nested=True):
                    lines.extend(text.split('\n'))
                    lines.append('')
        while lines and not lines[-1].strip():
            lines.pop()
        return lines

    def table_blocks(tbl, base, nested=False):
        if not nested:
            counter['table'] += 1
        number = counter['table']
        out = []
        for r_index, tr in enumerate(tbl.iterchildren(qn('w:tr')), 1):
            location = ('%s table, row %d' % (base, r_index)) if nested else 'Table %d, row %d' % (number, r_index)
            cells = []
            for tc in tr.iterchildren(qn('w:tc')):
                lines = cell_lines(tc, location)
                if any(x.strip() for x in lines):
                    cells.append(lines)
            if not cells:
                continue
            if len(cells) == 1:
                out.append(('\n'.join(cells[0]), location, ''))
                continue
            first = cells[0]
            rest = cells[1:]
            label = first[0].strip() if len(first) == 1 and len(first[0]) <= 90 else ''
            short = sum(len(' '.join(c)) for c in rest) <= 220
            if label and short:
                values = [' / '.join(x.strip() for x in c if x.strip()) for c in rest]
                out.append((' | '.join([label] + values), location, label))
            elif label:
                for c in rest:
                    out.append(('\n'.join(c), location, label))
            else:
                out.append((' | '.join(' / '.join(x.strip() for x in c if x.strip()) for c in cells), location, ''))
        return out

    for child in document.element.body.iterchildren():
        if child.tag == qn('w:p'):
            text = para_text(child)
            if text:
                blocks.append((text, 'Body', ''))
        elif child.tag == qn('w:tbl'):
            blocks.extend(table_blocks(child, ''))
    seen = set()
    for section in document.sections:
        for part, name in ((section.header, 'Header'), (section.footer, 'Footer')):
            for paragraph in part.paragraphs:
                text = paragraph.text.strip()
                if text and (name, text) not in seen:
                    seen.add((name, text))
                    blocks.append((text, name, ''))
    parts, marks, offset = [], [], 0
    for text, location, label in blocks:
        marks.append({'offset': offset, 'location': location, 'label': label})
        parts.append(text + '\n\n')
        offset += len(text) + 2
    return ''.join(parts), marks


def segment(text, max_clauses=4000, marks=None):
    """Split text into clauses with stable anchors. Deterministic and bounded.

    ``marks`` (optional, from a structured reader) adds a ``location`` and ``label`` to every clause; a
    "Requirement N:" line sets the ``context`` of the clauses that follow it inside the same block.
    """
    clauses, buffer, section, offset = [], [], '', 0
    start = 0
    mark_offsets = [m['offset'] for m in (marks or [])]
    state = {'context': '', 'mark': -1}

    def mark_at(position):
        if not mark_offsets:
            return -1
        return bisect.bisect_right(mark_offsets, position) - 1

    def flush():
        nonlocal buffer, start
        if not buffer:
            return
        raw = ' '.join(part.strip() for part in buffer).strip()
        buffer = []
        if len(tokens(raw)) < 3 and not (' | ' in raw and len(tokens(raw)) >= 2):   # keep short "Label | value" rows
            return
        match = REF_PATTERN.match(raw)
        number = NUMBERED.match(raw)
        ref = normalize_ref(match.group(1)) if match else ''
        if not ref:
            inline = INLINE_REF.search(raw)
            ref = normalize_ref(inline.group(1)) if inline and raw.lower().startswith(inline.group(1).lower()[:3]) else ''
        label = number.group(1) if number else ''
        if len(clauses) < max_clauses:
            index = mark_at(start)
            mark = marks[index] if index >= 0 else {}
            clauses.append({'index': len(clauses), 'ref': ref, 'section_number': label, 'section': section,
                            'text': raw, 'anchor': start, 'norm': normalize(raw), 'quote_hash': digest(normalize(raw)),
                            'location': mark.get('location', ''), 'label': mark.get('label', ''), 'context': state['context']})

    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if not stripped:
            flush()
        else:
            here = mark_at(offset)
            if here != state['mark']:
                flush()
                state['mark'] = here
                state['context'] = ''      # a new table cell or row starts a new block of requirements
            named = LABEL_HEADING.match(stripped)
            if named:
                flush()
                state['context'] = re.sub(r'\s+', ' ', named.group(1)).strip().title()
                offset += len(line)
                continue
            titled = LABEL_WITH_TITLE.match(stripped) if len(stripped) < 300 else None
            if titled and not MODAL.search(titled.group(2)) and len(tokens(titled.group(2))) <= 24:
                flush()
                state['context'] = re.sub(r'\s+', ' ', titled.group(1)).strip().title()
                state['title'] = titled.group(2).strip()
                offset += len(line)
                continue
            starts_new = bool(REF_PATTERN.match(stripped) or NUMBERED.match(stripped) or re.match(r'^[-*\u2022]\s+\S', stripped))
            is_heading = bool(NUMBERED.match(stripped)) and len(stripped) < 90 and not MODAL.search(stripped) and not stripped.endswith('.')
            if is_heading:
                flush()
                section = stripped
            elif starts_new and buffer:
                flush()
            if not buffer:
                start = offset
            if not is_heading:
                buffer.append(stripped)
        offset += len(line)
    flush()
    return clauses
