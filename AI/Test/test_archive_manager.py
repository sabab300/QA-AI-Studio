from Core.archive_manager import ArchiveManager

manager = ArchiveManager()

result = manager.archive_file(

    r"C:\Users\abdul.basit\Documents\QA AI Agent\AI\Repository\PSW Domain\SD Warehousing\SD Warehousing SRS\1.0\Documents\SRS_-_SD_-_Warehousing_v0.5 - Clean Copy.docx"

)

print("=" * 80)
print("ARCHIVE")
print("=" * 80)

print(result)