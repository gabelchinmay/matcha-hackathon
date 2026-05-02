from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # GMI Cloud – LLM inference
    # TODO: Confirm base URL with GMI Cloud sponsor docs
    gmi_api_key: str = ""
    gmi_base_url: str = "https://api.gmi-serving.com/v1"
    gmi_model: str = "anthropic/claude-haiku-4.5"
    gmi_infra_api_key: str = ""  # ce_resource scope — used for PixVerse video via GMI Cloud

    # HydraDB – memory/context layer
    # TODO: Confirm exact base URL from HydraDB sponsor docs
    hydradb_api_key: str = ""
    hydradb_project_id: str = ""
    hydradb_secret_key: str = ""
    hydradb_base_url: str = "https://api.hydradb.com"

    # PixVerse – video generation
    # TODO: Confirm PixVerse API token (may differ from GMI token)
    pixverse_api_key: str = ""
    pixverse_base_url: str = "https://app-api.pixverse.ai/openapi/v2"

    # Photon – bridge HTTP server (photon-bridge/bridge.js)
    photon_bridge_url: str = "http://localhost:3001"

    # Demo target
    on_call_phone: str = "+15555550100"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


@lru_cache
def get_settings() -> Settings:
    return Settings()
