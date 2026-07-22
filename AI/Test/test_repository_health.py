from Core.repository_health import RepositoryHealth

health = RepositoryHealth()

print("=" * 80)
print("REPOSITORY HEALTH")
print("=" * 80)

for item in health.check():

    print(item)