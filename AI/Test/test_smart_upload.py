from Core.smart_upload import SmartUpload

uploader = SmartUpload()

result = uploader.analyze(

    r"Repository\PSW Domain\SD Warehousing\SD Warehousing SRS\1.0\Documents\SRS_-_SD_-_Warehousing_v0.5 - Clean Copy.docx"

)

print("=" * 80)
print("SMART UPLOAD")
print("=" * 80)

for key, value in result.items():

    print(f"{key}: {value}")