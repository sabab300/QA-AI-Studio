from pathlib import Path
import hashlib


class FileIntegrity:

    @staticmethod
    def sha256(file_path):

        file_path = Path(file_path)

        sha = hashlib.sha256()

        with open(file_path, "rb") as file:

            while True:

                data = file.read(8192)

                if not data:

                    break

                sha.update(data)

        return sha.hexdigest()

    def verify(self, file_path, expected_hash):

        actual = self.sha256(file_path)

        return {

            "valid": actual == expected_hash,

            "expected": expected_hash,

            "actual": actual

        }