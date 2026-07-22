from Core.repository_import import RepositoryImport

manager = RepositoryImport()

result = manager.import_repository(

    r"C:\Users\abdul.basit\Documents\QA AI Agent\AI\Repository_Export"

)

print("=" * 80)
print("REPOSITORY IMPORT")
print("=" * 80)

print(result)