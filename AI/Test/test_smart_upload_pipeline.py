from Core.upload_pipeline import UploadPipeline

pipeline = UploadPipeline()

result = pipeline.smart_upload(

    r"C:\Users\abdul.basit\Documents\QA AI Agent\AI\Repository\PSW Domain\SD Warehousing\SD Warehousing SRS\1.0\Documents\SRS_-_SD_-_Warehousing_v0.5 - Clean Copy.docx"

)

print("=" * 80)
print("SMART UPLOAD PIPELINE")
print("=" * 80)

for key, value in result.items():
    print(f"{key}: {value}")