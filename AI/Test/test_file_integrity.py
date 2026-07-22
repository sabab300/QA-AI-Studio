from Core.file_integrity import FileIntegrity

checker = FileIntegrity()

file_path = (
    r"C:\Users\abdul.basit\Documents\QA AI Agent\AI\Repository\PSW Domain"
    r"\SD Warehousing\SD Warehousing SRS\1.0\Documents"
    r"\SRS_-_SD_-_Warehousing_v0.5 - Clean Copy.docx"
)

expected = "bede540b110bf3fc5c3499c1fafdce963a18ccae0d4b8f0f4f9fd7eb4040767d"

result = checker.verify(
    file_path,
    expected
)

print("=" * 80)
print("FILE INTEGRITY")
print("=" * 80)

for key, value in result.items():

    print(f"{key}: {value}")