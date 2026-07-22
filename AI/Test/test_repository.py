from Core.repository_manager import RepositoryManager

repo = RepositoryManager()

result = repo.save_file("requirements.txt")

print(result)