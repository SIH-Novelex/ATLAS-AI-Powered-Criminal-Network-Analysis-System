import os
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    APP_NAME: str = "Case-Graph-Intelligence-Backend"
    APP_ENV: str = "development"
    APP_HOST: str = "127.0.0.1"
    APP_PORT: int = 8000
    LOG_LEVEL: str = "INFO"
    DEBUG: bool = True

    # Neo4j configuration
    NEO4J_URI: str = Field(default="bolt://localhost:7687")
    NEO4J_USERNAME: str = Field(default="neo4j")
    NEO4J_PASSWORD: str = Field(default="password")
    NEO4J_DATABASE: str = Field(default="neo4j")
    NEO4J_MAX_CONNECTION_POOL_SIZE: int = 50

    # Path finding and algorithm defaults
    DEFAULT_MAX_PATH_DEPTH: int = 5
    ENABLE_GDS: bool = True

    # AI Intelligence (Gemini)
    GEMINI_API_KEY: Optional[str] = Field(default=None)


settings = Settings()

