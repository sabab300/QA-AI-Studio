"""
QA AI Studio
Production Text Extractor
Version: 2.0
"""

from pathlib import Path

from pypdf import PdfReader
from docx import Document
from openpyxl import load_workbook

from Core.logger import Logger


class TextExtractor:

    def __init__(self):

        self.logger = Logger.get_logger()

    # --------------------------------------------------
    # Extract Text
    # --------------------------------------------------

    def extract_text(self, file_path):

        file_path = Path(file_path)

        if not file_path.exists():
            raise FileNotFoundError(file_path)

        extension = file_path.suffix.lower()

        self.logger.info(
            f"Extracting text from: {file_path.name}"
        )

        if extension == ".pdf":

            text = self._extract_pdf(file_path)

        elif extension == ".docx":

            text = self._extract_docx(file_path)

        elif extension == ".txt":

            text = self._extract_txt(file_path)

        elif extension == ".xlsx":

            text = self._extract_xlsx(file_path)

        else:

            raise ValueError(
                f"Unsupported file type: {extension}"
            )

        text = text.strip()

        self.logger.info(
            f"Extracted {len(text):,} characters."
        )

        return text

    # --------------------------------------------------
    # PDF
    # --------------------------------------------------

    def _extract_pdf(self, file_path):

        reader = PdfReader(file_path)

        pages = []

        for page in reader.pages:

            page_text = page.extract_text()

            if page_text:

                pages.append(page_text)

        return "\n\n".join(pages)

    # --------------------------------------------------
    # DOCX
    # --------------------------------------------------

    def _extract_docx(self, file_path):
        """Read a Word document in reading order.

        Fixes three problems that damaged Change Request Forms:
        * a merged table cell was returned once per grid column it spans, so one
          requirements cell was repeated up to 8 times;
        * tables were appended after ALL paragraphs, losing the document order;
        * rows of 'Label | value' were flattened, and ticked boxes ([x]) lost their meaning.
        """
        from docx.oxml.ns import qn
        from docx.table import Table
        from docx.text.paragraph import Paragraph

        document = Document(file_path)
        parts = []

        def cell_lines(cell):
            lines = []
            for child in cell._tc.iterchildren():
                if child.tag == qn("w:p"):
                    text = Paragraph(child, cell).text.strip()
                    if text:
                        lines.append(text)
                elif child.tag == qn("w:tbl"):
                    lines.extend(table_lines(Table(child, cell)))
            return lines

        def unique_cells(row):
            seen, cells = set(), []
            for cell in row.cells:
                if id(cell._tc) in seen:
                    continue
                seen.add(id(cell._tc))
                cells.append(cell)
            return cells

        def checkbox_row(texts):
            marks = {"[x]": True, "[ ]": False, "\u2612": True, "\u2610": False, "\u2611": True}
            if len(texts) >= 2 and len(texts) % 2 == 0 and all(t.strip().lower() in marks for t in texts[1::2]):
                return [(texts[i], marks[texts[i + 1].strip().lower()]) for i in range(0, len(texts), 2)]
            return None

        def table_lines(table):
            lines, previous = [], None
            for row in table.rows:
                cells = unique_cells(row)
                blocks = [cell_lines(cell) for cell in cells]
                filled = [block for block in blocks if block]
                if not filled:
                    continue
                flat = ["; ".join(block) for block in filled]
                ticks = checkbox_row([" ".join(block) for block in blocks]) if len(blocks) == len(filled) else None
                if ticks is not None:
                    chosen = [label for label, on in ticks if on]
                    lines.append("Selected: " + (", ".join(chosen) if chosen else "none"))
                elif len(filled) == 1:
                    lines.extend(filled[0])
                elif len(filled) == 2 and len(filled[0]) == 1 and len(filled[0][0]) <= 120:
                    first, *rest = filled[1]
                    label = filled[0][0].rstrip(": ")
                    lines.append(f"{label}: {first}" if first else label)
                    lines.extend(rest)
                else:
                    lines.append(" | ".join(flat))
                if lines and lines[-1] == previous:
                    lines.pop()
                previous = lines[-1] if lines else None
            return lines

        body = document.element.body
        for child in body.iterchildren():
            if child.tag == qn("w:p"):
                text = Paragraph(child, document).text.strip()
                if text:
                    parts.append(text)
            elif child.tag == qn("w:tbl"):
                parts.extend(table_lines(Table(child, document)))

        pictures = len(document.inline_shapes)
        if pictures:
            self.logger.warning(
                "%s contains %d embedded picture(s). Text inside pictures is not extracted; "
                "provide that content as text or a table if tests must cover it.",
                Path(file_path).name, pictures,
            )

        for section in document.sections:
            for paragraph in section.header.paragraphs:
                text = paragraph.text.strip()
                if text and text not in parts:
                    parts.append(text)
            for paragraph in section.footer.paragraphs:
                text = paragraph.text.strip()
                if text and text not in parts:
                    parts.append(text)

        return "\n".join(parts)

    # --------------------------------------------------
    # TXT
    # --------------------------------------------------

    def _extract_txt(self, file_path):

        with open(
            file_path,
            "r",
            encoding="utf-8",
            errors="ignore"
        ) as file:

            return file.read()

    # --------------------------------------------------
    # XLSX
    # --------------------------------------------------

    def _extract_xlsx(self, file_path):

        workbook = load_workbook(
            file_path,
            data_only=True
        )

        lines = []

        for sheet in workbook.worksheets:

            lines.append(f"Sheet: {sheet.title}")

            for row in sheet.iter_rows(values_only=True):

                values = []

                for value in row:

                    if value is not None:

                        values.append(str(value))

                if values:

                    lines.append(" | ".join(values))

        return "\n".join(lines)