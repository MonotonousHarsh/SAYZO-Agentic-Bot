"""Application settings, read once from environment variables or the repo-root .env file."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # LLM (Groq)
    groq_api_key: SecretStr | None = None
    groq_model: str = "openai/gpt-oss-120b"
    groq_base_url: str = "https://api.groq.com/openai/v1"
    groq_timeout_seconds: float = Field(default=60.0, gt=0)
    # Free-tier friendly pacing for bulk jobs such as graph extraction. 0 disables pacing.
    groq_requests_per_minute: float = Field(default=25.0, ge=0)

    # Database (Supabase Postgres, session pooler URI)
    database_url: SecretStr | None = None

    # Embeddings (Hugging Face model run locally through sentence-transformers). The dimension
    # must match the vector column created by the migrations.
    embedding_model: str = "BAAI/bge-base-en-v1.5"
    embedding_dimension: int = 768
    embedding_device: str = "cpu"

    # Answering: chunks below this cosine similarity are not shown to the model (unless the
    # graph linked them to the question). Unrelated text scores about 0.40 with bge.
    answer_min_similarity: float = Field(default=0.5, ge=0, le=1)


@lru_cache
def get_settings() -> Settings:
    return Settings()
