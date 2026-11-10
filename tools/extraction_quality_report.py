"""Print the extraction-quality report for one or more source documents.

Usage:  python tools/extraction_quality_report.py <file.docx> [<file2.docx> ...]
Read-only: it never changes the files, the database or the knowledge store.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for candidate in (ROOT / "AI-Web", ROOT):
    if (candidate / "Core").is_dir():
        sys.path.insert(0, str(candidate))
        break

from Core.extraction_quality import analyse, format_report  # noqa: E402


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    for name in argv:
        print(format_report(analyse(name)))
        print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
