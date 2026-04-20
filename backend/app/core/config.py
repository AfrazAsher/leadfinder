from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = Field(default="development")
    log_level: str = Field(default="INFO")

    # Optional now, required in later stages
    supabase_url: Optional[str] = None
    supabase_service_role_key: Optional[str] = None
    supabase_anon_key: Optional[str] = None
    database_url: Optional[str] = None

    google_api_key: Optional[str] = None
    google_search_engine_id: Optional[str] = None
    opencorporates_api_token: Optional[str] = None
    apollo_api_key: Optional[str] = None


settings = Settings()
