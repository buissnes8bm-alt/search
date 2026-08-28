"""Configuration, read from the environment.

Every secret arrives as an environment variable so the same code runs under
GitHub Actions (secrets injected by the runner) and on your laptop (a .env you
never commit). Nothing is read from a file that could end up in git.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


class ConfigError(RuntimeError):
    """A required setting is missing or unusable."""


def _require(name: str) -> str:
    value = (os.environ.get(name) or "").strip()
    if not value:
        raise ConfigError(
            f"{name} is not set. In GitHub Actions add it under "
            f"Settings -> Secrets and variables -> Actions. Locally, export it."
        )
    return value


@dataclass(frozen=True)
class Config:
    serper_api_key: str
    gemini_api_key: str
    gemini_model: str
    telegram_token: str
    telegram_chat_id: str
    sheet_id: str
    sheet_tab: str
    service_account_json: str

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            serper_api_key=_require("SERPER_API_KEY"),
            gemini_api_key=_require("GEMINI_API_KEY"),
            # Overridable so you can try a Pro model without touching code.
            gemini_model=os.environ.get("GEMINI_MODEL", "gemini-2.0-flash").strip(),
            telegram_token=_require("TELEGRAM_BOT_TOKEN"),
            telegram_chat_id=_require("TELEGRAM_CHAT_ID"),
            sheet_id=_require("SHEET_ID"),
            sheet_tab=os.environ.get("SHEET_TAB", "Sheet1").strip() or "Sheet1",
            service_account_json=_require("GOOGLE_SERVICE_ACCOUNT_JSON"),
        )


# Tunables. These were constants in the n8n Code nodes; they are constants here
# for the same reason — you should have to mean it to change them.
SCORE_THRESHOLD = 6
TOP_N = 5
BLOB_CHAR_CAP = 12_000
RESULTS_PER_QUERY = 10
QUERIES_PER_RUN = 4
ROTATION_STEP = 5
RETENTION_DAYS = 90
MAX_MEMORY = 500
MAX_OUTPUT_TOKENS = 4000
HTTP_TIMEOUT = 60
RETRY_ATTEMPTS = 3
RETRY_WAIT_SECONDS = 5
