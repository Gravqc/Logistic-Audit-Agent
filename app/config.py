from pydantic_settings import BaseSettings
from functools import lru_cache
from typing import Optional

class Settings(BaseSettings):
    # PostgreSQL
    POSTGRES_URL: str = "postgresql+asyncpg://freight:freight@localhost:5432/freight"

    # Neo4j
    NEO4J_URI: str = "bolt://localhost:7687"
    NEO4J_USER: str = "neo4j"
    NEO4J_PASSWORD: str = "password"

    # LLM — provider-agnostic
    LLM_PROVIDER: str = "google"   # "anthropic" | "openai" | "google"
    # Using Gemini model matching the google provider
    LLM_MODEL: str = "gemini-3-flash-preview"
    LLM_API_KEY: Optional[str] = None
    GEMINI_API_KEY: Optional[str] = None

    # Agent behaviour
    CONFIDENCE_AUTO_APPROVE_THRESHOLD: float = 80.0
    CONFIDENCE_DISPUTE_THRESHOLD: float = 50.0
    RATE_DRIFT_TOLERANCE_PERCENT: float = 2.0

    class Config:
        env_file = ".env"
        extra = "allow"

    @property
    def active_llm_api_key(self) -> str:
        # Fallback to GEMINI_API_KEY if LLM_API_KEY is not set
        key = self.LLM_API_KEY or self.GEMINI_API_KEY
        if not key:
            raise ValueError("LLM_API_KEY or GEMINI_API_KEY must be set")
        return key

@lru_cache
def get_settings() -> Settings:
    return Settings()
