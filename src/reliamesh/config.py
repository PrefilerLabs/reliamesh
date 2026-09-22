"""Explicit, fail-closed service configuration."""

import os
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    store: str = "sqlite"
    sqlite_path: str = ".local/reliamesh.db"
    project: str = "reliamesh"
    database: str = "(default)"
    admin_hash: str = ""
    max_body_bytes: int = 131072
    daily_events: int = 10000
    requests_per_minute: int = 120
    global_requests_per_second: int = 10

    def __post_init__(self):
        if self.store not in {"sqlite", "firestore"}:
            raise ValueError("RM_STORE must be sqlite or firestore")
        if self.store == "firestore" and self.project != "reliamesh":
            raise ValueError("Cloud project must be exactly reliamesh")
        if self.admin_hash and not re.fullmatch(r"[0-9a-f]{64}", self.admin_hash):
            raise ValueError("RM_ADMIN_HASH must be a SHA256 digest")
        if not 1 <= self.daily_events <= 100000:
            raise ValueError("daily event limit must be 1..100000")

    @classmethod
    def from_env(cls):
        return cls(
            store=os.getenv("RM_STORE", "sqlite"),
            sqlite_path=os.getenv("RM_SQLITE_PATH", ".local/reliamesh.db"),
            project=os.getenv("RM_GCP_PROJECT", "reliamesh"),
            database=os.getenv("RM_DATABASE", "(default)"),
            admin_hash=os.getenv("RM_ADMIN_HASH", ""),
            daily_events=int(os.getenv("RM_DAILY_EVENTS", "10000")),
        )


def make_store(settings):
    from reliamesh.storage import FirestoreStore, SQLiteStore

    if settings.store == "firestore":
        return FirestoreStore(project=settings.project, database=settings.database)
    return SQLiteStore(settings.sqlite_path)
