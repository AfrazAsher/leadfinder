from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = Field(default="development")
    log_level: str = Field(default="INFO")
    max_parallel_entities: int = Field(default=8)

    google_api_key: Optional[str] = None
    google_search_engine_id: Optional[str] = None
    opencorporates_api_token: Optional[str] = None
    apollo_api_key: Optional[str] = None


settings = Settings()
