"""Settings, read from the environment (.env)."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    # groq | openai_compatible (any OpenAI-style gateway, e.g. APINEX)
    provider: str = os.getenv("LLM_PROVIDER", "openai_compatible")
    model: str = os.getenv("LLM_MODEL", "free/gpt-6-luna")
    groq_api_key: str = os.getenv("GROQ_API_KEY", "")
    openai_api_key: str = os.getenv("OPENAI_COMPAT_API_KEY", "")
    openai_base_url: str = os.getenv("OPENAI_COMPAT_BASE_URL", "")
    company_app_url: str = os.getenv("COMPANY_APP_URL", "http://127.0.0.1:8765")
    headless: bool = os.getenv("HEADLESS", "1") == "1"
    # Folders the worker may read. Anything else is refused.
    workspace: Path = ROOT / "company_data"
    runs_dir: Path = ROOT / "runs"
    max_steps: int = int(os.getenv("MAX_STEPS", "120"))
    max_consecutive_failures: int = 3
    max_verify_attempts: int = 2


settings = Settings()
