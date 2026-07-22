"""
QA AI Studio
Repository Manager

Version: 4.0
"""

import hashlib
import shutil
from pathlib import Path


class RepositoryManager:

    def __init__(self):

        self.repository_root = (
            Path(__file__).resolve().parent.parent
            / "Repository"
        )

        self.repository_root.mkdir(
            parents=True,
            exist_ok=True
        )

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

        version

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

            "extension": destination.suffix.lower()

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

        if folder.exists():

            shutil.rmtree(folder)

            return True

        return False

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