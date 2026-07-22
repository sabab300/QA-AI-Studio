from Core.repository_export import RepositoryExport

exporter = RepositoryExport()

result = exporter.export(

    r"C:\Users\abdul.basit\Documents\QA AI Agent\AI\Repository_Export"

)

print("=" * 80)
print("REPOSITORY EXPORT")
print("=" * 80)

print(result)