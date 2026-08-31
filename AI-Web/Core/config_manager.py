import json
from pathlib import Path


class ConfigManager:
    """
    Central configuration manager for QA AI Studio.
    Responsible for:
    - Project paths
    - Configuration files
    - Directory creation
    - Master data loading
    """

    def __init__(self):

        # AI/
        self.base_path = Path(__file__).resolve().parent.parent

        # Core folders
        self.config_path = self.base_path / "Config"
        self.database_path = self.base_path / "Database"
        self.repository_path = self.base_path / "Repository"
        self.knowledge_path = self.base_path / "Knowledge"
        self.models_path = self.base_path / "Models"
        self.output_path = self.base_path / "Output"
        self.templates_path = self.base_path / "Templates"
        self.prompts_path = self.base_path / "Prompts"

        # Database files
        self.metadata_db = self.database_path / "metadata.db"
        self.chroma_db = self.database_path / "chroma_db"

        # Configuration files
        self.master_data_file = self.config_path / "master_data.json"

        # Ensure required folders exist
        self._create_directories()

        # Load configuration
        self.master_data = self._load_master_data()

    # --------------------------------------------------

    def _create_directories(self):

        folders = [

            self.database_path,
            self.repository_path,
            self.knowledge_path,
            self.models_path,
            self.output_path,
            self.templates_path,
            self.prompts_path,
            self.chroma_db

        ]

        for folder in folders:
            folder.mkdir(parents=True, exist_ok=True)

    # --------------------------------------------------

    def _load_master_data(self):

        if not self.master_data_file.exists():
            return {}

        with open(
            self.master_data_file,
            "r",
            encoding="utf-8"
        ) as file:

            return json.load(file)

    # --------------------------------------------------

    def get_master_list(self, key):

        return self.master_data.get(key, [])