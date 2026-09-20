import argparse
import getpass
import json
from pathlib import Path

from governance_app.config import AppConfig
from governance_app.identity_store import IdentityStore
from governance_app.online_migration import migrate_sqlite_to_postgres


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Online governance platform administration"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    migrate = subparsers.add_parser(
        "migrate",
        help="migrate an SQLite workspace database to PostgreSQL",
    )
    migrate.add_argument("--sqlite", required=True, type=Path)
    migrate.add_argument("--postgres-url", required=True)
    migrate.add_argument("--dry-run", action="store_true")
    platform = subparsers.add_parser(
        "provision-platform-admin",
        help="create a separate platform settings administrator",
    )
    platform.add_argument("--postgres-url", required=True)
    platform.add_argument("--username", required=True)
    args = parser.parse_args()
    if args.command == "migrate":
        report = migrate_sqlite_to_postgres(
            args.sqlite,
            args.postgres_url,
            dry_run=args.dry_run,
        )
        print(json.dumps({
            "source_counts": report.source_counts,
            "target_counts": report.target_counts,
            "migrated_rows": report.migrated_rows,
            "dry_run": report.dry_run,
        }, ensure_ascii=False))
    elif args.command == "provision-platform-admin":
        password = getpass.getpass("Platform admin password (12+ characters): ")
        config = AppConfig.for_online_workspace(
            Path.cwd(), database_url=args.postgres_url,
            object_store_bucket="not-used-for-provisioning",
        )
        store = IdentityStore(config)
        store.initialize()
        store.provision_platform_admin(username=args.username, password=password)
        print("Platform administrator created")


if __name__ == "__main__":
    main()
