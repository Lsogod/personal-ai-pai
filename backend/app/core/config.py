from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "PAI"
    api_prefix: str = "/api"

    database_url: str = "postgresql+asyncpg://pai_user:password@db:5432/pai_db"
    database_url_sync: str = "postgresql+psycopg2://pai_user:password@db:5432/pai_db"
    redis_url: str = "redis://redis:6379/0"

    openai_base_url: str = "https://api.openai.com/v1"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o"
    vision_model: str = "gpt-4o"

    timezone: str = "Asia/Shanghai"
    log_level: str = "INFO"

    # Webhook verification / auth (optional)
    webhook_secret: str = ""

    # Messaging bridges
    telegram_bot_token: str = ""
    telegram_webhook_secret: str = ""
    telegram_polling_enabled: bool = False
    telegram_polling_interval: int = 2
    telegram_polling_timeout: int = 25
    telegram_polling_limit: int = 50
    feishu_app_id: str = ""
    feishu_app_secret: str = ""
    feishu_verification_token: str = ""
    feishu_encrypt_key: str = ""
    feishu_receive_id_type: str = "open_id"
    bridge_base_url: str = "http://bridge"
    onebot_base_url: str = "http://napcat:3000"
    onebot_access_token: str = ""
    gewechat_base_url: str = "http://gewechat:2531"
    gewechat_app_id: str = ""
    gewechat_token: str = ""
    miniapp_app_id: str = ""
    miniapp_app_secret: str = ""
    miniapp_subscribe_template_id: str = ""
    miniapp_page_path: str = "pages/chat/index"
    miniapp_lang: str = "zh_CN"
    miniapp_subscribe_content_key: str = "thing1"
    miniapp_subscribe_time_key: str = "time2"

    scheduler_enabled: bool = True
    allow_memory_checkpointer_fallback: bool = False

    mcp_fetch_enabled: bool = True
    mcp_fetch_url: str = ""
    mcp_fetch_timeout_sec: int = 30
    mcp_fetch_default_max_length: int = 5000

    long_term_memory_enabled: bool = True
    long_term_memory_min_confidence: float = 0.75
    long_term_memory_max_write_items: int = 6
    long_term_memory_retrieve_limit: int = 6
    long_term_memory_retrieve_scan_limit: int = 80
    long_term_memory_default_ttl_days: int = 180

    admin_token: str = ""
    dedup_ttl_seconds: int = 86400

    jwt_secret: str = "change_me"
    jwt_algorithm: str = "HS256"
    jwt_exp_minutes: int = 60 * 24 * 7


@lru_cache
def get_settings() -> Settings:
    return Settings()
