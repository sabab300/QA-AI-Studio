from Core.knowledge_dashboard import KnowledgeDashboard

dashboard = KnowledgeDashboard()

print("=" * 80)
print("KNOWLEDGE DASHBOARD")
print("=" * 80)

for key, value in dashboard.summary().items():

    print(f"{key}: {value}")