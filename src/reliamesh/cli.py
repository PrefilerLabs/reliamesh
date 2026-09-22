"""Operator CLI. Credentials are written once to a user-chosen private file."""

import argparse
import json
import os
import re
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from reliamesh.config import Settings, make_store
from reliamesh.security import digest_key, new_key
from reliamesh.storage import SCHEMA_VERSION


def write_private(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Never overwrite a credential file. POSIX mode is private; on Windows keep
    # the containing directory private using the operating system's ACLs.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
        handle.write("\n")


def backup_sqlite(source_path, output_path):
    """Back up an existing supported database without creating a missing source."""
    source_path, output_path = Path(source_path).expanduser().resolve(), Path(output_path)
    # mode=ro fails on a missing source instead of creating a plausible empty backup.
    with closing(sqlite3.connect(source_path.as_uri() + "?mode=ro", uri=True)) as source:
        if source.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
            raise ValueError("source is not a supported ReliaMesh database")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(output_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        try:
            with closing(sqlite3.connect(output_path)) as target:
                source.backup(target)
        except BaseException:
            # Only remove the output this invocation exclusively created.
            output_path.unlink(missing_ok=True)
            raise


def main():
    parser = argparse.ArgumentParser(description="ReliaMesh operator CLI")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="Start the API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8080)
    tenant = commands.add_parser("tenant").add_subparsers(dest="action", required=True)
    create = tenant.add_parser("create")
    create.add_argument("tenant_id")
    create.add_argument("--output", required=True)
    delete = tenant.add_parser("delete")
    delete.add_argument("tenant_id")
    delete.add_argument("--confirm", required=True)
    commands.add_parser("purge", help="Physically remove expired local state")
    bootstrap = commands.add_parser("admin-key", help="Generate an administrator credential")
    bootstrap.add_argument("--output", required=True)
    backup = commands.add_parser("backup", help="Consistent SQLite backup; contains private data")
    backup.add_argument("--output", required=True)
    args = parser.parse_args()
    settings = Settings.from_env()
    if args.command == "serve":
        import uvicorn

        uvicorn.run("reliamesh.main:app", host=args.host, port=args.port, access_log=False)
        return
    if args.command == "admin-key":
        key = new_key()
        write_private(args.output, {"key": key, "sha256": digest_key(key)})
        print("Administrator credential written. Set RM_ADMIN_HASH to its sha256 value.")
        return
    if args.command == "backup":
        if settings.store != "sqlite":
            parser.error("Use managed Firestore backup/PITR for the cloud backend")
        backup_sqlite(settings.sqlite_path, args.output)
        print("Consistent private SQLite backup created.")
        return
    if args.command == "purge" and settings.store != "sqlite":
        parser.error("purge supports SQLite only; cloud state expiry uses Firestore TTL")
    store = make_store(settings)
    if args.command == "purge":
        print(json.dumps({"purged": store.purge_expired()}))
        return
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{2,63}", args.tenant_id):
        parser.error("Tenant ID must be 3..64 lowercase letters, digits, underscores or hyphens")
    if args.action == "create":
        if Path(args.output).exists():
            parser.error("Output file exists; choose a new private credential path")
        key = new_key()
        store.create_tenant(args.tenant_id, digest_key(key), datetime.now(UTC))
        try:
            write_private(args.output, {"tenant_id": args.tenant_id, "key": key, "scopes": ["ingest", "read", "manage"]})
        except Exception:
            store.delete_tenant(args.tenant_id)
            raise
        print("Tenant created; credential written to private output file.")
    elif args.action == "delete":
        if args.confirm != args.tenant_id:
            parser.error("--confirm must exactly match the tenant ID")
        print(json.dumps({"deleted": store.delete_tenant(args.tenant_id)}))


if __name__ == "__main__":
    main()
