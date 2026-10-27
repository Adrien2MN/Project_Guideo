"""Settings loaded from environment variables (and the .env file if present)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    prim_api_key: str | None
    data_dir: Path
    velib_poll_seconds: int

    @property
    def velib_db_path(self) -> Path:
        return self.data_dir / "velib.sqlite"

    @property
    def streets_dir(self) -> Path:
        return self.data_dir / "streets"

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"


def load_settings() -> Settings:
    load_dotenv()
    data_dir = Path(os.getenv("GUIDEO_DATA_DIR", "./data")).expanduser().resolve()
    return Settings(
        prim_api_key=os.getenv("PRIM_API_KEY") or None,
        data_dir=data_dir,
        velib_poll_seconds=int(os.getenv("VELIB_POLL_SECONDS", "60")),
    )
