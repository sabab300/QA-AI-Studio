"""
QA AI Studio
Repository Manager

Version: 4.0
"""

import hashlib
import re
import shutil
from pathlib import Path


class RepositoryManager:

    def __init__(self):

        self.repository_root = (
            Path(__file__).resolve().parents[2]
            / "AI-Web"
            / "Repository"
        )

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
        document_type=""
    ):

        source = Path(source_file)

        destination_folder = self.get_documents_folder(

            domain,

            module,

            knowledge_name,

            version

        )

        destination = (

            destination_folder

            / source.name

        )

        destination_folder.mkdir(
            parents=True,
            exist_ok=True
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

            "document_type": document_type

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
