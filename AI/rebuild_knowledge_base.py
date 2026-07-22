"""
QA AI Studio
Knowledge Base Rebuilder
Version: 1.0
"""

from pathlib import Path
from time import perf_counter

from Core.upload_pipeline import UploadPipeline
from Core.vector_store import VectorStore


SUPPORTED_EXTENSIONS = {

    ".pdf",
    ".docx",
    ".txt",
    ".xlsx"

}


def main():

    start_time = perf_counter()

    pipeline = UploadPipeline()

    vector_store = VectorStore()

    print("\n========================================")
    print(" QA AI STUDIO - KNOWLEDGE BASE REBUILD ")
    print("========================================\n")

    print("Clearing existing vector database...")

    if not vector_store.clear():

        print("Failed to clear collection.")
        return

    print("Done.\n")

    repository = Path("Repository")

    if not repository.exists():

        print("Repository folder not found.")
        return

    files = []

    for extension in SUPPORTED_EXTENSIONS:

        files.extend(
            repository.rglob(f"*{extension}")
        )

    files.sort()

    print(f"Documents Found : {len(files)}\n")

    total_vectors = 0
    success = 0
    failed = 0

    for index, file in enumerate(files, start=1):

        print(
            f"[{index}/{len(files)}] {file.name}"
        )

        try:

            result = pipeline.upload(
                str(file)
            )

            if result["success"]:

                success += 1

                total_vectors += result["vectors_saved"]

                print(
                    f"   ✓ Chunks : {result['total_chunks']}"
                )

            else:

                failed += 1

                print("   ✗ Failed")

        except Exception as error:

            failed += 1

            print(
                f"   ✗ {error}"
            )

    elapsed = perf_counter() - start_time

    print("\n========================================")
    print(" KNOWLEDGE BASE REBUILD COMPLETED ")
    print("========================================")

    print(f"Documents Processed : {success}")
    print(f"Documents Failed    : {failed}")
    print(f"Vectors Created     : {total_vectors}")
    print(f"Collection Count    : {vector_store.count()}")
    print(f"Time                : {elapsed:.2f} sec")

    print("========================================")


if __name__ == "__main__":

    main()