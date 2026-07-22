from Core.repository_statistics import RepositoryStatistics

stats = RepositoryStatistics()

result = stats.summary()

print("=" * 80)
print("REPOSITORY STATISTICS")
print("=" * 80)

for key, value in result.items():

    print(f"{key}: {value}")