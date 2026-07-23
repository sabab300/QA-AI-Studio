"""
QA AI Studio
Memory Manager

Version: 3.0
Production Ready
"""

from datetime import datetime

from Core.sqlite_manager import SQLiteManager
from Core.logger import Logger



class MemoryManager:


    TABLE_NAME = "ai_memory"



    def __init__(self):


        self.logger = Logger.get_logger()

        self.database = SQLiteManager()


        self._create_table()

        self._migrate()



    # --------------------------------------------------
    # Create Table
    # --------------------------------------------------

    def _create_table(self):


        query = f"""

        CREATE TABLE IF NOT EXISTS {self.TABLE_NAME}

        (

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            session_id TEXT,

            role TEXT,

            prompt TEXT,

            response TEXT,

            intent TEXT,

            source TEXT,

            domain TEXT,

            module TEXT,

            knowledge_name TEXT,

            confidence REAL,

            execution_time REAL,

            created_at TEXT

        )

        """


        self.database.execute_non_query(
            query
        )



    # --------------------------------------------------
    # Database Migration
    # --------------------------------------------------

    def _migrate(self):


        columns = {


            "session_id": "TEXT",

            "role": "TEXT",

            "intent": "TEXT",

            "source": "TEXT",

            "domain": "TEXT",

            "module": "TEXT",

            "knowledge_name": "TEXT",

            "confidence": "REAL",

            "execution_time": "REAL"


        }



        for column, datatype in columns.items():


            if not self.database.column_exists(

                self.TABLE_NAME,

                column

            ):


                self.logger.info(

                    f"Adding memory column: {column}"

                )


                self.database.execute_non_query(

                    f"""

                    ALTER TABLE {self.TABLE_NAME}

                    ADD COLUMN {column} {datatype}

                    """

                )



    # --------------------------------------------------
    # Save Memory
    # --------------------------------------------------

    def save(

        self,

        prompt,

        response,

        session_id="default",

        role="user",

        intent="answer",

        source="RAG",

        domain=None,

        module=None,

        knowledge_name=None,

        confidence=0,

        execution_time=0

    ):


        query = f"""

        INSERT INTO {self.TABLE_NAME}

        (

            session_id,

            role,

            prompt,

            response,

            intent,

            source,

            domain,

            module,

            knowledge_name,

            confidence,

            execution_time,

            created_at

        )

        VALUES

        (

            ?,?,?,?,?,?,?,?,?,?,?,?

        )

        """



        self.database.execute_insert(

            query,

            (

                session_id,

                role,

                prompt,

                response,

                intent,

                source,

                domain,

                module,

                knowledge_name,

                confidence,

                execution_time,

                datetime.now().isoformat()

            )

        )



    # --------------------------------------------------
    # Recent Memory
    # --------------------------------------------------

    def get_recent(

        self,

        session_id="default",

        limit=10

    ):


        query = f"""

        SELECT

            prompt,

            response,

            intent,

            confidence,

            created_at


        FROM {self.TABLE_NAME}


        WHERE session_id=?


        ORDER BY id DESC


        LIMIT ?

        """



        return self.database.fetch_all(

            query,

            (

                session_id,

                limit

            )

        )



    # --------------------------------------------------
    # Search Memory
    # --------------------------------------------------

    def search(

        self,

        keyword,

        limit=10

    ):


        query = f"""

        SELECT

            prompt,

            response,

            intent,

            created_at


        FROM {self.TABLE_NAME}


        WHERE prompt LIKE ?


        ORDER BY id DESC


        LIMIT ?

        """



        return self.database.fetch_all(

            query,

            (

                f"%{keyword}%",

                limit

            )

        )



    # --------------------------------------------------
    # Clear Session
    # --------------------------------------------------

    def clear(

        self,

        session_id="default"

    ):


        self.database.execute_non_query(

            f"""

            DELETE FROM {self.TABLE_NAME}

            WHERE session_id=?

            """,

            (

                session_id,

            )

        )



    # --------------------------------------------------
    # Count
    # --------------------------------------------------

    def count(self):


        row = self.database.fetch_one(

            f"""

            SELECT COUNT(*)

            FROM {self.TABLE_NAME}

            """

        )


        return row[0] if row else 0
    
    # ==================================================
    # Domain CRUD
    # ==================================================

    def create_domain(self, name):

        pass


    def list_domains(self):

        pass


    def update_domain(self, domain_id, name):

        pass


    def delete_domain(self, domain_id):

        pass


    # ==================================================
    # Module CRUD
    # ==================================================

    def create_module(self, domain_name, module_name):

        pass


    def list_modules(self, domain_name):

        pass


    def update_module(self, module_id, module_name):

        pass


    def delete_module(self, module_id):

        pass