from Core.repository_dashboard import RepositoryDashboard

dashboard = RepositoryDashboard()

print("=" * 80)
print("REPOSITORY DASHBOARD")
print("=" * 80)

result = dashboard.summary()

for key, value in result.items():

    print(f"{key}: {value}")