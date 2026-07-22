from Core.restore_manager import RestoreManager

manager = RestoreManager()

result = manager.restore(

    "SRS_-_SD_-_Warehousing_v0.5 - Clean Copy.docx",

    r"C:\Users\abdul.basit\Documents\QA AI Agent\AI\Restore_Test"

)

print("=" * 80)
print("RESTORE")
print("=" * 80)

print(result)