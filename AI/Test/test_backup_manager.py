from Core.backup_manager import BackupManager

manager = BackupManager()

print("=" * 80)
print("CREATE BACKUP")
print("=" * 80)

backup = manager.backup()

print(backup)

print("\n" + "=" * 80)
print("AVAILABLE BACKUPS")
print("=" * 80)

for item in manager.list_backups():

    print(item)