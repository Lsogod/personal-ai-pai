from functools import lru_cache
from pydantic import Field
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
    mcp_fetch_api_key: str = ""
    web_search_api_url: str = "https://open.bigmodel.cn/api/paas/v4/web_search"
    web_search_api_key: str = ""
    web_search_engine: str = "search_pro_sogou"
    web_search_intent: bool = True
    web_search_content_size: str = "medium"
    mcp_search_url: str = ""
    mcp_search_api_key: str = ""
    mcp_search_primary_tool_name: str = "web_search_prime"
    mcp_search_content_size: str = "medium"
    mcp_search_fallback_url: str = ""
    mcp_search_fallback_api_key: str = ""
    mcp_maps_url: str = ""
    mcp_fetch_timeout_sec: int = 30
    mcp_fetch_default_max_length: int = 5000
    # Comma-separated MCP tool allowlist for non-maps tools. Empty means allow all.
    mcp_allowed_tool_names: str = ""
    # Comma-separated MCP tool allowlist for search_* style MCP tools. Empty means allow all.
    mcp_search_allowed_tool_names: str = "bing_search,crawl_webpage"
    # Comma-separated MCP tool allowlist for maps_* tools. Empty means allow all.
    mcp_maps_allowed_tool_names: str = "maps_weather"
    runtime_tool_cache_ttl_sec: int = 300
    # complex_task DAG executor controls
    complex_task_max_parallel: int = 4
    complex_task_dependency_wait_cycles: int = 1
    complex_task_dependency_wait_ms: int = 120
    complex_task_tool_call_limit: int = 8
    complex_task_tool_per_action_limit: int = 4
    complex_task_agent_recursion_limit: int = 8
    complex_task_plan_timeout_sec: int = 5
    complex_task_followup_timeout_sec: int = 8

    rebind_intent_timeout_sec: int = 2
    router_intent_timeout_sec: int = 10
    preload_runtime_tools_on_startup: bool = False
    preload_graph_on_startup: bool = False

    long_term_memory_enabled: bool = True
    long_term_memory_retrieve_mode: str = "full_inject"
    long_term_memory_min_confidence: float = 0.5
    long_term_memory_max_write_items: int = 6
    long_term_memory_retrieve_limit: int = 20
    long_term_memory_retrieve_scan_limit: int = 80
    long_term_memory_default_ttl_days: int = 730
    long_term_memory_debounce_sec: int = 0
    long_term_memory_extract_timeout_sec: int = 90
    long_term_memory_upsert_timeout_sec: int = 90
    long_term_memory_extract_context_max_chars: int = 24000
    long_term_memory_extract_message_max_chars: int = 200
    long_term_memory_scan_enabled: bool = True
    long_term_memory_scan_run_in_api: bool = False
    long_term_memory_scan_interval_sec: int = 120
    long_term_memory_scan_max_conversations: int = 80
    long_term_memory_scan_max_messages_per_conversation: int = 30
    memory_index_worker_enabled: bool = False
    memory_index_worker_interval_sec: int = 30
    memory_index_worker_batch_size: int = 32
    memory_embedding_model: str = "text-embedding-3-small"
    memory_embedding_dim: int = 1536
    memory_vector_version: int = 1
    milvus_enabled: bool = Field(default=False, validation_alias="MEMORY_MILVUS_ENABLED")
    milvus_uri: str = Field(default="", validation_alias="MEMORY_MILVUS_URI")
    milvus_token: str = Field(default="", validation_alias="MEMORY_MILVUS_TOKEN")
    milvus_collection: str = Field(default="memory_text_v1", validation_alias="MEMORY_MILVUS_COLLECTION")
    milvus_search_limit: int = 24
    startup_preload_timeout_sec: int = 8

    admin_token: str = ""
    dedup_ttl_seconds: int = 86400
    jwt_secret: str = "change_me"
    jwt_algorithm: str = "HS256"
    jwt_exp_minutes: int = 60 * 24 * 7

    smtp_host: str = ""
    smtp_port: int = 465
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from_email: str = ""
    smtp_from_name: str = "PAI"
    smtp_use_ssl: bool = True
    smtp_use_starttls: bool = False

    auth_email_code_ttl_sec: int = 600
    auth_email_code_cooldown_sec: int = 60
    auth_email_code_max_verify_attempts: int = 8


@lru_cache
def get_settings() -> Settings:
    return Settings()
