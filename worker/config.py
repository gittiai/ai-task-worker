"""Settings, read from the environment (.env)."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    groq_api_key: str = os.getenv("GROQ_API_KEY", "")
    model: str = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
    company_app_url: str = os.getenv("COMPANY_APP_URL", "http://127.0.0.1:8765")
    headless: bool = os.getenv("HEADLESS", "1") == "1"
    # Folders the worker may read. Anything else is refused.
    workspace: Path = ROOT / "company_data"
    runs_dir: Path = ROOT / "runs"
    max_steps: int = int(os.getenv("MAX_STEPS", "60"))
    max_consecutive_failures: int = 3
    max_verify_attempts: int = 2


settings = Settings()
