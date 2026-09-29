"""Settings read from environment variables."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_path: str = os.environ.get("BOOKSHELF_DB", "bookshelf.db")
    secret_key: str = os.environ.get("BOOKSHELF_SECRET", "dev-secret-change-me")
    token_ttl_seconds: int = int(os.environ.get("BOOKSHELF_TOKEN_TTL", "3600"))
    max_active_loans: int = 3
    loan_period_days: int = 14


settings = Settings()
