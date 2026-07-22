from Core.upload_pipeline import UploadPipeline

DOCUMENT = r"D:\OneDrive - Pakistan Single Window\All PSW\SQA PSW\PSW Products & Integration Testing\PRA\SETN\CRF-SET&NC-02-03162026.docx"
# or .pdf

pipeline = UploadPipeline()

result = pipeline.upload(DOCUMENT)

print("\n========== RESULT ==========")

for key, value in result.items():
    print(f"{key}: {value}")