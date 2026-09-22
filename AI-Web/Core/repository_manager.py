"""
QA AI Studio
Repository Manager

Version: 4.0
"""

import hashlib
import re
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from Shared.central_repository import CentralRepository


class RepositoryManager:

    def __init__(self):

        self.central = CentralRepository()
        self.repository_root = self.central.root

        self.repository_root.mkdir(
            parents=True,
            exist_ok=True
        )

    _WINDOWS_RESERVED_NAMES = {
        "CON", "PRN", "AUX", "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }

    @classmethod
    def _safe_segment(cls, value, label):
        """Return a stable filesystem-safe segment without changing display metadata."""

        original = str(value or "").strip()
        if not original or original in {".", ".."}:
            raise ValueError(f"Invalid {label}.")

        # Windows rejects control characters and: < > : " / \ | ? *
        sanitized = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', original)
        sanitized = sanitized.rstrip(' .')

        if not sanitized or sanitized in {".", ".."}:
            raise ValueError(f"Invalid {label}.")

        # Reserved device names are invalid even when an extension is present.
        stem = sanitized.split('.', 1)[0].upper()
        if stem in cls._WINDOWS_RESERVED_NAMES:
            sanitized = f"_{sanitized}"

        # Keep paths manageable while making long values deterministic.
        if len(sanitized) > 120:
            digest = hashlib.sha256(original.encode("utf-8")).hexdigest()[:10]
            sanitized = f"{sanitized[:105].rstrip(' ._')}_{digest}"

        return sanitized

    # --------------------------------------------------
    # Repository Path
    # --------------------------------------------------

    def get_repository_path(

        self,

        domain,

        module,

        knowledge_name,

        version

    ):

        domain = self._safe_segment(domain, "domain")
        module = self._safe_segment(module, "module")
        knowledge_name = self._safe_segment(knowledge_name, "knowledge name")
        version = self._safe_segment(version, "version")

        path = (

            self.repository_root

            / domain

            / module

            / knowledge_name

            / version

        )

        path.mkdir(

            parents=True,

            exist_ok=True

        )

        return path

    # --------------------------------------------------
    # Documents Folder
    # --------------------------------------------------

    def get_documents_folder(

        self,

        domain,

        module,

        knowledge_name,

        version

    ):

        folder = (

            self.get_repository_path(

                domain,

                module,

                knowledge_name,

                version

            )

            / "Documents"

        )

        folder.mkdir(

            parents=True,

            exist_ok=True

        )

        return folder

    # --------------------------------------------------
    # URLs Folder
    # --------------------------------------------------

    def get_urls_folder(

        self,

        domain,

        module,

        knowledge_name,

        version

    ):

        folder = (

            self.get_repository_path(

                domain,

                module,

                knowledge_name,

                version

            )

            / "URLs"

        )

        folder.mkdir(

            parents=True,

            exist_ok=True

        )

        return folder

    # --------------------------------------------------
    # Notes Folder
    # --------------------------------------------------

    def get_notes_folder(

        self,

        domain,

        module,

        knowledge_name,

        version

    ):

        folder = (

            self.get_repository_path(

                domain,

                module,

                knowledge_name,

                version

            )

            / "Notes"

        )

        folder.mkdir(

            parents=True,

            exist_ok=True

        )

        return folder

    # --------------------------------------------------
    # SQL Folder
    # --------------------------------------------------

    def get_sql_folder(

        self,

        domain,

        module,

        knowledge_name,

        version

    ):

        folder = (

            self.get_repository_path(

                domain,

                module,

                knowledge_name,

                version

            )

            / "SQL"

        )

        folder.mkdir(

            parents=True,

            exist_ok=True

        )

        return folder

    # --------------------------------------------------
    # API Folder
    # --------------------------------------------------

    def get_api_folder(

        self,

        domain,

        module,

        knowledge_name,

        version

    ):

        folder = (

            self.get_repository_path(

                domain,

                module,

                knowledge_name,

                version

            )

            / "APIs"

        )

        folder.mkdir(

            parents=True,

            exist_ok=True

        )

        return folder

    # --------------------------------------------------
    # SHA256
    # --------------------------------------------------

    def calculate_sha256(

        self,

        file_path

    ):

        sha = hashlib.sha256()

        with open(

            file_path,

            "rb"

        ) as file:

            while True:

                chunk = file.read(8192)

                if not chunk:
                    break

                sha.update(chunk)

        return sha.hexdigest()

    # --------------------------------------------------
    # Save File
    # --------------------------------------------------

    def save_file(
        self,
        source_file,
        domain,
        module,
        knowledge_name,
        version,
        document_type="",
        source_type="Files",
        source_file_id=None,
    ):

        source = Path(source_file)

        source_file_id = source_file_id or self.central.create_source_file_id(source.name)
        destination_folder = self.central.build_source_path(
            domain=domain, module=module, knowledge_name=knowledge_name,
            version=version, document_type=document_type or "Other",
            source_type=source_type or "Files", source_file_id=source_file_id,
        )

        destination = (

            destination_folder

            / source.name

        )

        # QA-AI-STUDIO-FINAL-ARCHITECTURE-UI-AUTOMATION-COMPLETION-V4
        # Rule 2: a Source Document (the SD-* folder) is a REAL managed
        # container that can legitimately hold more than one physical
        # Source File — the rule's own worked example is exactly this:
        # SD-CRF-123-01122026/{CRF-123-01122026.docx,
        # CRF-123-01122026.pdf}. Both files share the same filename
        # stem, so central.create_source_file_id()/build_source_path()
        # correctly resolve them to the SAME destination_folder — that
        # is the intended behavior, not a collision.
        #
        # The previous check here (`if destination_folder.exists():
        # raise ValueError(...)`) treated the SECOND file into an
        # already-created Source Document folder as an error, which
        # made that documented multi-file scenario impossible: the
        # first upload created the folder, so every subsequent upload
        # into the same Source Document — even a different file —
        # failed immediately. The only real collision to guard against
        # is the SAME file name being uploaded twice into the SAME
        # Source Document, which is exactly what checking `destination`
        # (the actual target file path), not `destination_folder` (the
        # container), already does below.
        destination_folder.mkdir(
            parents=True,
            exist_ok=True
        )

        if destination.exists():
            raise FileExistsError(
                f"'{source.name}' already exists in this Source Document "
                f"({destination_folder.name})."
            )
        shutil.copy2(

            source,

            destination

        )

        return {

            "repository_path": str(destination),

            "sha256": self.calculate_sha256(destination),

            "file_name": source.name,

            "file_size": destination.stat().st_size,

            "extension": destination.suffix.lower(),

            "document_type": document_type,

            "source_file_id": source_file_id,

            "repository_relative_path": self.central.relative(destination),

        }

    # --------------------------------------------------
    # Save Folder
    # --------------------------------------------------

    def save_folder(

        self,

        folder,

        domain,

        module,

        knowledge_name,

        version

    ):

        results = []

        folder = Path(folder)

        for file in folder.rglob("*"):

            if file.is_file():

                results.append(

                    self.save_file(

                        str(file),

                        domain,

                        module,

                        knowledge_name,

                        version

                    )

                )

        return results

    # --------------------------------------------------
    # Save URL
    # --------------------------------------------------

    def save_url(

        self,

        url,

        domain,

        module,

        knowledge_name,

        version

    ):

        folder = self.get_urls_folder(

            domain,

            module,

            knowledge_name,

            version

        )

        file = folder / "urls.txt"

        with open(

            file,

            "a",

            encoding="utf-8"

        ) as writer:

            writer.write(url + "\n")

        return str(file)

    # --------------------------------------------------
    # Save Note
    # --------------------------------------------------

    def save_note(

        self,

        note,

        domain,

        module,

        knowledge_name,

        version

    ):

        folder = self.get_notes_folder(

            domain,

            module,

            knowledge_name,

            version

        )

        file = folder / "notes.txt"

        with open(

            file,

            "a",

            encoding="utf-8"

        ) as writer:

            writer.write(note)

            writer.write("\n\n")

        return str(file)

    # --------------------------------------------------
    # Delete Knowledge
    # --------------------------------------------------

    def delete_knowledge(
        self,
        domain,
        module,
        knowledge_name
    ):

        folder = (
            self.repository_root
            / domain
            / module
            / knowledge_name
        )

        print("Repository Root :", self.repository_root)
        print("Delete Folder :", repr(str(folder)))
        print("Folder Exists :", folder.exists())

        if not folder.exists():

            return (
                True,
                0
            )

        try:

            file_count = sum(
                1
                for item in folder.rglob("*")
                if item.is_file()
            )

            shutil.rmtree(folder)

            print("Folder Exists After Delete :", folder.exists())

            return (
                True,
                file_count
            )

        except Exception as ex:

            return (
                False,
                str(ex)
            )

    # --------------------------------------------------
    # List Files
    # --------------------------------------------------

    def list_files(

        self,

        domain,

        module,

        knowledge_name,

        version

    ):

        folder = self.get_repository_path(

            domain,

            module,

            knowledge_name,

            version

        )

        return [

            str(file)

            for file in folder.rglob("*")

            if file.is_file()

        ]
