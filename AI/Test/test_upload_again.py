from Core.upload_pipeline import UploadPipeline


pipeline = UploadPipeline()


result = pipeline.upload_file(
    source_file=r"D:\QA AI Studio-Repository\PSW Domain\SD Warehousing\SD Warehousing SRS\1.0\Documents\SRS_-_SD_-_Warehousing_v0.5 - Clean Copy.docx",
    domain="PSW Domain",
    module="SD Warehousing",
    knowledge_name="SD Warehousing SRS",
    version="1.0"
)


print(result)