import os
from dataclasses import dataclass
from urllib.parse import urlparse


ALLOWED_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    app_name: str = "OpenCode to OpenWebUI Orchestrator"
    db_path: str = "./data/orchestrator.db"
    opencode_base_url: str = "http://127.0.0.1:4096"
    opencode_username: str = "opencode"
    opencode_password: str = ""
    orch_api_key: str = ""

    allow_missing_owui_headers: bool = False
    default_user_id: str = "local-user"
    default_chat_id: str = "manual-chat"

    connect_timeout_s: float = 10.0
    read_timeout_s: float = 1800.0
    write_timeout_s: float = 60.0
    pool_timeout_s: float = 30.0
    sync_wait_timeout_s: float = 90.0

    event_listener_enabled: bool = True
    event_reconnect_min_s: float = 1.0
    event_reconnect_max_s: float = 10.0
    use_structured_output: bool = False
    log_level: str = "INFO"


def validate_local_base_url(base_url: str) -> None:
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"}:
        raise RuntimeError("OPENCODE_BASE_URL must start with http:// or https://")
    if not parsed.hostname or parsed.hostname not in ALLOWED_LOCAL_HOSTS:
        raise RuntimeError(
            "OPENCODE_BASE_URL must point to a local address only "
            "(127.0.0.1, localhost, or ::1)."
        )


def load_settings_from_env() -> Settings:
    settings = Settings(
        app_name=os.getenv("ORCH_APP_NAME", "OpenCode to OpenWebUI Orchestrator"),
        db_path=os.getenv("ORCH_DB_PATH", "./data/orchestrator.db"),
        opencode_base_url=os.getenv("OPENCODE_BASE_URL", "http://127.0.0.1:4096").rstrip("/"),
        opencode_username=os.getenv("OPENCODE_SERVER_USERNAME", "opencode"),
        opencode_password=os.getenv("OPENCODE_SERVER_PASSWORD", ""),
        orch_api_key=os.getenv("ORCH_API_KEY", ""),
        allow_missing_owui_headers=env_bool("ALLOW_MISSING_OWUI_HEADERS", False),
        default_user_id=os.getenv("DEFAULT_OWUI_USER_ID", "local-user"),
        default_chat_id=os.getenv("DEFAULT_OWUI_CHAT_ID", "manual-chat"),
        connect_timeout_s=float(os.getenv("OPENCODE_CONNECT_TIMEOUT_S", "10")),
        read_timeout_s=float(os.getenv("OPENCODE_READ_TIMEOUT_S", "1800")),
        write_timeout_s=float(os.getenv("OPENCODE_WRITE_TIMEOUT_S", "60")),
        pool_timeout_s=float(os.getenv("OPENCODE_POOL_TIMEOUT_S", "30")),
        sync_wait_timeout_s=float(os.getenv("SYNC_WAIT_TIMEOUT_S", "90")),
        event_listener_enabled=env_bool("ENABLE_OPENCODE_EVENT_LISTENER", True),
        event_reconnect_min_s=float(os.getenv("EVENT_RECONNECT_MIN_S", "1")),
        event_reconnect_max_s=float(os.getenv("EVENT_RECONNECT_MAX_S", "10")),
        use_structured_output=env_bool("USE_STRUCTURED_OUTPUT", False),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
    )
    validate_local_base_url(settings.opencode_base_url)
    return settings
