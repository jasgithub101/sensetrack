"""Settings loaded from .env at the project root."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
IMAGE_DIR = PROJECT_ROOT / "backend" / "data" / "images"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    mongodb_uri: str
    mongodb_db: str = "sensetrack"

    groq_api_key: str = ""
    # Groq rotates its catalogue often. This one supports both JSON mode (intent
    # extraction) and plain chat (answer rendering); the gpt-oss models on Groq
    # currently fail JSON mode. Run scripts/check_setup.py to list what your key
    # can actually reach.
    groq_model: str = "qwen/qwen3.8-27b"

    host: str = "0.0.0.0"
    port: int = 8000


settings = Settings()
