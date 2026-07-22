"""
QA AI Studio
Upload Pipeline Test
"""

from Core.upload_pipeline import UploadPipeline


def main():

    pipeline = UploadPipeline()

    result = pipeline.upload(
        "requirements.txt"
    )

    print("\n========== RESULT ==========\n")

    print(result)

    print("\n========== FILE ==========\n")

    print(result["file_info"])

    print("\n========== ANALYSIS ==========\n")

    print(result["analysis"])

    print("\n========== CHUNK INFORMATION ==========\n")

    print(f"Total Chunks : {result['total_chunks']}")
    print(f"Vectors Saved: {result['vectors_saved']}")

    print("\n========== STATUS ==========\n")

    if result["success"]:

        print("Upload Pipeline Test : PASSED")

    else:

        print("Upload Pipeline Test : FAILED")


if __name__ == "__main__":

    main()