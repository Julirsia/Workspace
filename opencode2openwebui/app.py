import asyncio
import hashlib
import json
import logging
import os
import re
import sqlite3
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional, Tuple
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


ALLOWED_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


@dataclass(frozen=True)
class Settings:
    app_name: str = os.getenv("ORCH_APP_NAME", "OpenCode to OpenWebUI Orchestrator")
    db_path: str = os.getenv("ORCH_DB_PATH", "./data/orchestrator.db")
    opencode_base_url: str = os.getenv("OPENCODE_BASE_URL", "http://127.0.0.1:4096").rstrip("/")
    opencode_username: str = os.getenv("OPENCODE_SERVER_USERNAME", "opencode")
    opencode_password: str = os.getenv("OPENCODE_SERVER_PASSWORD", "")
    orch_api_key: str = os.getenv("ORCH_API_KEY", "")

    allow_missing_owui_headers: bool = env_bool("ALLOW_MISSING_OWUI_HEADERS", False)
    default_user_id: str = os.getenv("DEFAULT_OWUI_USER_ID", "local-user")
    default_chat_id: str = os.getenv("DEFAULT_OWUI_CHAT_ID", "manual-chat")

    connect_timeout_s: float = float(os.getenv("OPENCODE_CONNECT_TIMEOUT_S", "10"))
    read_timeout_s: float = float(os.getenv("OPENCODE_READ_TIMEOUT_S", "1800"))
    write_timeout_s: float = float(os.getenv("OPENCODE_WRITE_TIMEOUT_S", "60"))
    pool_timeout_s: float = float(os.getenv("OPENCODE_POOL_TIMEOUT_S", "30"))
    sync_wait_timeout_s: float = float(os.getenv("SYNC_WAIT_TIMEOUT_S", "90"))

    event_listener_enabled: bool = env_bool("ENABLE_OPENCODE_EVENT_LISTENER", True)
    event_reconnect_min_s: float = float(os.getenv("EVENT_RECONNECT_MIN_S", "1"))
    event_reconnect_max_s: float = float(os.getenv("EVENT_RECONNECT_MAX_S", "10"))
    use_structured_output: bool = env_bool("USE_STRUCTURED_OUTPUT", False)
    log_level: str = os.getenv("LOG_LEVEL", "INFO").upper()


SETTINGS = Settings()


def validate_local_base_url(base_url: str) -> None:
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"}:
        raise RuntimeError("OPENCODE_BASE_URL must start with http:// or https://")
    if not parsed.hostname or parsed.hostname not in ALLOWED_LOCAL_HOSTS:
        raise RuntimeError(
            "OPENCODE_BASE_URL must point to a local address only "
            "(127.0.0.1, localhost, or ::1)."
        )


validate_local_base_url(SETTINGS.opencode_base_url)

logging.basicConfig(
    level=getattr(logging, SETTINGS.log_level, logging.INFO),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
log = logging.getLogger("opencode2openwebui")


class ModelSelector(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    provider_id: Optional[str] = Field(default=None, alias="providerID")
    model_id: Optional[str] = Field(default=None, alias="modelID")

    def to_opencode(self) -> Optional[Dict[str, str]]:
        if not self.provider_id or not self.model_id:
            return None
        return {"providerID": self.provider_id, "modelID": self.model_id}


class RequestContext(BaseModel):
    user_id: str
    chat_id: str
    message_id: Optional[str] = None


class SessionEnsureRequest(BaseModel):
    workspace_root: Optional[str] = None
    slot: str = "default"
    title: Optional[str] = None
    preferred_agent: Optional[str] = None
    auto_create: bool = True


class SessionAttachRequest(BaseModel):
    opencode_session_id: str
    workspace_root: Optional[str] = None
    slot: str = "default"
    title: Optional[str] = None
    preferred_agent: Optional[str] = None
    archive_existing: bool = True


class SessionArchiveRequest(BaseModel):
    session_ref: str


class SessionForkRequest(BaseModel):
    session_ref: Optional[str] = None
    workspace_root: Optional[str] = None
    slot: str = "default"
    new_slot: str = "experiment"
    title: Optional[str] = None
    message_id: Optional[str] = None


class TaskRequest(BaseModel):
    session_ref: Optional[str] = None
    workspace_root: Optional[str] = None
    slot: str = "default"
    title: Optional[str] = None
    prompt: str
    briefing: Optional[str] = None
    agent: Optional[str] = None
    model: Optional[ModelSelector] = None
    create_if_missing: bool = True
    include_diff: bool = True
    include_todos: bool = True
    max_messages_for_status: int = 6


class StatusRequest(BaseModel):
    session_ref: Optional[str] = None
    workspace_root: Optional[str] = None
    slot: str = "default"
    create_if_missing: bool = False
    include_diff: bool = True
    include_todos: bool = True
    max_messages: int = 6


class PermissionReplyRequest(BaseModel):
    session_ref: Optional[str] = None
    workspace_root: Optional[str] = None
    slot: str = "default"
    permission_id: str
    response: Literal["once", "always", "reject"]


class AbortRequest(BaseModel):
    session_ref: Optional[str] = None
    workspace_root: Optional[str] = None
    slot: str = "default"


class DiffRequest(BaseModel):
    session_ref: Optional[str] = None
    workspace_root: Optional[str] = None
    slot: str = "default"
    message_id: Optional[str] = None


SCHEMA_SQL = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS session_binding (
  session_ref TEXT PRIMARY KEY,
  owui_user_id TEXT NOT NULL,
  owui_chat_id TEXT NOT NULL,
  workspace_root TEXT NOT NULL,
  slot TEXT NOT NULL DEFAULT 'default',
  opencode_base_url TEXT NOT NULL,
  opencode_session_id TEXT NOT NULL,
  title TEXT NOT NULL,
  preferred_agent TEXT,
  archived INTEGER NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  last_used_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_session_binding_chat
  ON session_binding(owui_user_id, owui_chat_id, archived, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_session_binding_opencode
  ON session_binding(opencode_session_id);

CREATE TABLE IF NOT EXISTS pending_permission (
  permission_id TEXT PRIMARY KEY,
  opencode_session_id TEXT NOT NULL,
  session_ref TEXT,
  state TEXT NOT NULL,
  permission_type TEXT,
  title TEXT,
  pattern_json TEXT,
  metadata_json TEXT,
  response TEXT,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_pending_permission_session
  ON pending_permission(opencode_session_id, state, updated_at DESC);

CREATE TABLE IF NOT EXISTS kv_state (
  key TEXT PRIMARY KEY,
  value_json TEXT NOT NULL,
  updated_at INTEGER NOT NULL
);
"""


class DB:
    def __init__(self, path: str):
        self.path = path
        self._lock = threading.RLock()
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(self.path)) or ".", exist_ok=True)
        with self._lock:
            with self._conn() as conn:
                conn.executescript(SCHEMA_SQL)
                conn.commit()

    def execute(self, sql: str, params: Tuple[Any, ...] = ()) -> None:
        with self._lock:
            with self._conn() as conn:
                conn.execute(sql, params)
                conn.commit()

    def fetchone(self, sql: str, params: Tuple[Any, ...] = ()) -> Optional[Dict[str, Any]]:
        with self._lock:
            with self._conn() as conn:
                row = conn.execute(sql, params).fetchone()
                return dict(row) if row else None

    def fetchall(self, sql: str, params: Tuple[Any, ...] = ()) -> List[Dict[str, Any]]:
        with self._lock:
            with self._conn() as conn:
                rows = conn.execute(sql, params).fetchall()
                return [dict(row) for row in rows]

    def archive_active_binding(self, ctx: RequestContext, workspace_root: str, slot: str) -> None:
        now = now_ms()
        self.execute(
            """
            UPDATE session_binding
            SET archived=1, updated_at=?, last_used_at=?
            WHERE owui_user_id=? AND owui_chat_id=? AND workspace_root=? AND slot=? AND archived=0
            """,
            (now, now, ctx.user_id, ctx.chat_id, workspace_root, slot),
        )

    def upsert_binding(
        self,
        *,
        session_ref: str,
        ctx: RequestContext,
        workspace_root: str,
        slot: str,
        opencode_session_id: str,
        title: str,
        preferred_agent: Optional[str] = None,
        archived: bool = False,
    ) -> Dict[str, Any]:
        now = now_ms()
        with self._lock:
            with self._conn() as conn:
                conn.execute(
                    """
                    INSERT INTO session_binding(
                      session_ref, owui_user_id, owui_chat_id, workspace_root, slot,
                      opencode_base_url, opencode_session_id, title, preferred_agent,
                      archived, created_at, updated_at, last_used_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(session_ref) DO UPDATE SET
                      owui_user_id=excluded.owui_user_id,
                      owui_chat_id=excluded.owui_chat_id,
                      workspace_root=excluded.workspace_root,
                      slot=excluded.slot,
                      opencode_base_url=excluded.opencode_base_url,
                      opencode_session_id=excluded.opencode_session_id,
                      title=excluded.title,
                      preferred_agent=excluded.preferred_agent,
                      archived=excluded.archived,
                      updated_at=excluded.updated_at,
                      last_used_at=excluded.last_used_at
                    """,
                    (
                        session_ref,
                        ctx.user_id,
                        ctx.chat_id,
                        workspace_root,
                        slot,
                        SETTINGS.opencode_base_url,
                        opencode_session_id,
                        title,
                        preferred_agent,
                        1 if archived else 0,
                        now,
                        now,
                        now,
                    ),
                )
                conn.commit()
        row = self.fetchone("SELECT * FROM session_binding WHERE session_ref=?", (session_ref,))
        if row is None:
            raise RuntimeError("failed to store session binding")
        return row

    def touch_binding(self, session_ref: str, preferred_agent: Optional[str] = None, title: Optional[str] = None) -> None:
        now = now_ms()
        sets = ["updated_at=?", "last_used_at=?"]
        params: List[Any] = [now, now]
        if preferred_agent is not None:
            sets.append("preferred_agent=?")
            params.append(preferred_agent)
        if title is not None:
            sets.append("title=?")
            params.append(title)
        params.append(session_ref)
        self.execute(
            "UPDATE session_binding SET {sets} WHERE session_ref=?".format(sets=", ".join(sets)),
            tuple(params),
        )

    def get_binding_by_ref(self, ctx: RequestContext, session_ref: str) -> Optional[Dict[str, Any]]:
        return self.fetchone(
            """
            SELECT * FROM session_binding
            WHERE session_ref=? AND owui_user_id=? AND archived=0
            """,
            (session_ref, ctx.user_id),
        )

    def get_binding_for_chat(self, ctx: RequestContext, workspace_root: str, slot: str) -> Optional[Dict[str, Any]]:
        return self.fetchone(
            """
            SELECT * FROM session_binding
            WHERE owui_user_id=? AND owui_chat_id=? AND workspace_root=? AND slot=? AND archived=0
            ORDER BY updated_at DESC LIMIT 1
            """,
            (ctx.user_id, ctx.chat_id, workspace_root, slot),
        )

    def list_bindings_for_chat(
        self,
        ctx: RequestContext,
        workspace_root: str,
        include_archived: bool = False,
    ) -> List[Dict[str, Any]]:
        params: List[Any] = [ctx.user_id, ctx.chat_id, workspace_root]
        sql = """
            SELECT * FROM session_binding
            WHERE owui_user_id=? AND owui_chat_id=? AND workspace_root=?
        """
        if not include_archived:
            sql += " AND archived=0"
        sql += " ORDER BY archived ASC, updated_at DESC"
        return self.fetchall(sql, tuple(params))

    def archive_binding(self, ctx: RequestContext, session_ref: str) -> bool:
        now = now_ms()
        self.execute(
            """
            UPDATE session_binding
            SET archived=1, updated_at=?, last_used_at=?
            WHERE session_ref=? AND owui_user_id=?
            """,
            (now, now, session_ref, ctx.user_id),
        )
        row = self.fetchone(
            "SELECT archived FROM session_binding WHERE session_ref=? AND owui_user_id=?",
            (session_ref, ctx.user_id),
        )
        return bool(row and row["archived"])

    def get_session_ref_by_opencode_session(self, opencode_session_id: str) -> Optional[str]:
        row = self.fetchone(
            """
            SELECT session_ref FROM session_binding
            WHERE opencode_session_id=? AND archived=0
            ORDER BY updated_at DESC LIMIT 1
            """,
            (opencode_session_id,),
        )
        return row["session_ref"] if row else None

    def upsert_permission(self, permission: Dict[str, Any]) -> None:
        permission_id = permission["id"]
        session_id = permission["sessionID"]
        session_ref = self.get_session_ref_by_opencode_session(session_id)
        now = now_ms()
        created = int(permission.get("time", {}).get("created", now))
        pattern_json = json.dumps(permission.get("pattern"), ensure_ascii=False)
        metadata_json = json.dumps(permission.get("metadata", {}), ensure_ascii=False)
        with self._lock:
            with self._conn() as conn:
                conn.execute(
                    """
                    INSERT INTO pending_permission(
                      permission_id, opencode_session_id, session_ref, state,
                      permission_type, title, pattern_json, metadata_json,
                      response, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(permission_id) DO UPDATE SET
                      opencode_session_id=excluded.opencode_session_id,
                      session_ref=excluded.session_ref,
                      state=excluded.state,
                      permission_type=excluded.permission_type,
                      title=excluded.title,
                      pattern_json=excluded.pattern_json,
                      metadata_json=excluded.metadata_json,
                      updated_at=excluded.updated_at
                    """,
                    (
                        permission_id,
                        session_id,
                        session_ref,
                        "pending",
                        permission.get("type"),
                        permission.get("title"),
                        pattern_json,
                        metadata_json,
                        None,
                        created,
                        now,
                    ),
                )
                conn.commit()

    def mark_permission_replied(self, permission_id: str, response: str) -> None:
        now = now_ms()
        self.execute(
            """
            UPDATE pending_permission
            SET state='replied', response=?, updated_at=?
            WHERE permission_id=?
            """,
            (response, now, permission_id),
        )

    def list_permissions(
        self,
        *,
        session_ref: Optional[str] = None,
        opencode_session_id: Optional[str] = None,
        state: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        clauses = ["1=1"]
        params: List[Any] = []
        if session_ref:
            clauses.append("session_ref=?")
            params.append(session_ref)
        if opencode_session_id:
            clauses.append("opencode_session_id=?")
            params.append(opencode_session_id)
        if state:
            clauses.append("state=?")
            params.append(state)
        rows = self.fetchall(
            """
            SELECT * FROM pending_permission
            WHERE {clauses}
            ORDER BY created_at DESC
            """.format(clauses=" AND ".join(clauses)),
            tuple(params),
        )
        for row in rows:
            try:
                row["pattern"] = json.loads(row.pop("pattern_json") or "null")
            except Exception:
                row["pattern"] = None
            try:
                row["metadata"] = json.loads(row.pop("metadata_json") or "{}")
            except Exception:
                row["metadata"] = {}
        return rows


db = DB(SETTINGS.db_path)


JSON_ONLY_SYSTEM_PROMPT = """You are an internal coding subagent used by Open WebUI.

Do the detailed work inside this OpenCode session. Keep intermediate tool work inside OpenCode.
Your final response must be exactly one JSON object and nothing else.

Schema:
{
  "summary": string,
  "deliverables": string[],
  "tests_run": [{"command": string, "status": string, "details": string}],
  "risks": string[],
  "next_action": string
}

Rules:
- Do not use markdown.
- Do not include prose before or after the JSON.
- Keep the summary short and factual.
- Use empty arrays if a field has no items.
"""


RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "deliverables": {"type": "array", "items": {"type": "string"}},
        "tests_run": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "status": {"type": "string"},
                    "details": {"type": "string"},
                },
                "required": ["command", "status", "details"],
            },
        },
        "risks": {"type": "array", "items": {"type": "string"}},
        "next_action": {"type": "string"},
    },
    "required": ["summary", "deliverables", "tests_run", "risks", "next_action"],
}


_FIXED_WORKSPACE_ROOT_CACHE: Optional[str] = None
_FIXED_WORKSPACE_ROOT_LOCK = asyncio.Lock()


def now_ms() -> int:
    return int(time.time() * 1000)


def normalize_path(path: str) -> str:
    return os.path.abspath(os.path.expanduser(path))


def compact_title(workspace_root: str, slot: str, explicit_title: Optional[str]) -> str:
    if explicit_title:
        return explicit_title.strip()
    base = os.path.basename(workspace_root.rstrip("/")) or "workspace"
    return "owui:{base}:{slot}".format(base=base, slot=slot)


def make_session_ref(user_id: str, chat_id: str, workspace_root: str, slot: str) -> str:
    raw = "{base}|{user}|{chat}|{root}|{slot}".format(
        base=SETTINGS.opencode_base_url,
        user=user_id,
        chat=chat_id,
        root=workspace_root,
        slot=slot,
    )
    return "oc_{digest}".format(digest=hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20])


def opencode_auth() -> Optional[httpx.BasicAuth]:
    if SETTINGS.opencode_password:
        return httpx.BasicAuth(SETTINGS.opencode_username, SETTINGS.opencode_password)
    return None


def request_to_ctx(request: Request) -> RequestContext:
    if SETTINGS.orch_api_key:
        supplied = request.headers.get("x-api-key", "")
        if supplied != SETTINGS.orch_api_key:
            raise HTTPException(status_code=401, detail="Invalid API key")

    user_id = request.headers.get("X-OpenWebUI-User-Id")
    chat_id = request.headers.get("X-OpenWebUI-Chat-Id")
    message_id = request.headers.get("X-OpenWebUI-Message-Id")

    if not user_id or not chat_id:
        if not SETTINGS.allow_missing_owui_headers:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Missing Open WebUI forwarded headers. "
                    "Enable ENABLE_FORWARD_USER_INFO_HEADERS=true in Open WebUI."
                ),
            )
        user_id = user_id or SETTINGS.default_user_id
        chat_id = chat_id or SETTINGS.default_chat_id

    return RequestContext(user_id=user_id, chat_id=chat_id, message_id=message_id)


def parts_to_text(parts: List[Dict[str, Any]]) -> str:
    texts: List[str] = []
    for part in parts:
        if part.get("type") == "text" and not part.get("ignored"):
            text = part.get("text")
            if isinstance(text, str) and text:
                texts.append(text)
    return "\n".join(texts).strip()


def parts_to_commands(parts: List[Dict[str, Any]]) -> List[str]:
    commands: List[str] = []
    for part in parts:
        if part.get("type") != "tool":
            continue
        tool_name = part.get("tool", "")
        state = part.get("state") or {}
        input_obj = state.get("input") or {}
        candidate = None
        for key in ("command", "cmd", "args", "pattern", "path", "query"):
            value = input_obj.get(key)
            if value:
                candidate = value
                break
        commands.append("{tool}: {candidate}".format(tool=tool_name, candidate=candidate) if candidate else tool_name)
    seen = set()
    ordered: List[str] = []
    for item in commands:
        if item not in seen:
            seen.add(item)
            ordered.append(item)
    return ordered


def parse_jsonish(text: str) -> Optional[Dict[str, Any]]:
    if not text:
        return None
    stripped = text.strip()
    try:
        data = json.loads(stripped)
        if isinstance(data, dict):
            return data
    except Exception:
        pass

    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, flags=re.S | re.I)
    if fenced:
        try:
            data = json.loads(fenced.group(1))
            if isinstance(data, dict):
                return data
        except Exception:
            pass

    start = stripped.find("{")
    if start == -1:
        return None

    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(stripped)):
        char = stripped[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                candidate = stripped[start:index + 1]
                try:
                    data = json.loads(candidate)
                    if isinstance(data, dict):
                        return data
                except Exception:
                    return None
    return None


def normalize_result_json(raw: Optional[Dict[str, Any]], fallback_text: str) -> Dict[str, Any]:
    if not raw:
        return {
            "summary": fallback_text[:2000] if fallback_text else "No structured summary produced.",
            "deliverables": [],
            "tests_run": [],
            "risks": [],
            "next_action": "",
        }
    return {
        "summary": str(raw.get("summary", "") or ""),
        "deliverables": [str(item) for item in raw.get("deliverables", []) if item is not None],
        "tests_run": [
            {
                "command": str(item.get("command", "")),
                "status": str(item.get("status", "")),
                "details": str(item.get("details", "")),
            }
            for item in raw.get("tests_run", [])
            if isinstance(item, dict)
        ],
        "risks": [str(item) for item in raw.get("risks", []) if item is not None],
        "next_action": str(raw.get("next_action", "") or ""),
    }


def binding_to_out(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "session_ref": row["session_ref"],
        "opencode_session_id": row["opencode_session_id"],
        "title": row["title"],
        "workspace_root": row["workspace_root"],
        "slot": row["slot"],
        "preferred_agent": row.get("preferred_agent"),
        "created_at": int(row["created_at"]),
        "updated_at": int(row["updated_at"]),
        "last_used_at": int(row["last_used_at"]),
        "archived": bool(row["archived"]),
    }


class OpenCodeHTTP:
    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=SETTINGS.opencode_base_url,
            auth=opencode_auth(),
            timeout=httpx.Timeout(
                connect=SETTINGS.connect_timeout_s,
                read=SETTINGS.read_timeout_s,
                write=SETTINGS.write_timeout_s,
                pool=SETTINGS.pool_timeout_s,
            ),
            headers={"Accept": "application/json"},
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def json_request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
        timeout: Optional[httpx.Timeout] = None,
        expected_status: Optional[int] = None,
    ) -> Any:
        try:
            response = await self._client.request(
                method,
                path,
                params=params,
                json=json_body,
                timeout=timeout,
            )
            response.raise_for_status()
            if expected_status is not None and response.status_code != expected_status:
                raise HTTPException(
                    status_code=502,
                    detail="Unexpected OpenCode status {status} for {method} {path}".format(
                        status=response.status_code,
                        method=method,
                        path=path,
                    ),
                )
            if response.status_code == 204 or not response.content:
                return None
            return response.json()
        except httpx.TimeoutException:
            raise
        except httpx.HTTPStatusError as exc:
            detail: Any = exc.response.text
            try:
                detail = exc.response.json()
            except Exception:
                pass
            raise HTTPException(
                status_code=502,
                detail={
                    "message": "OpenCode error during {method} {path}".format(method=method, path=path),
                    "status_code": exc.response.status_code,
                    "detail": detail,
                },
            ) from exc
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail="Failed to reach OpenCode: {error}".format(error=exc)) from exc

    async def health(self) -> Dict[str, Any]:
        return await self.json_request("GET", "/global/health")

    @staticmethod
    def _extract_path(payload: Any) -> Optional[str]:
        if isinstance(payload, str) and payload:
            return payload
        if not isinstance(payload, dict):
            return None
        for key in ("root", "path", "directory", "cwd"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
        for value in payload.values():
            nested = OpenCodeHTTP._extract_path(value)
            if nested:
                return nested
        return None

    async def current_project_root(self) -> Optional[str]:
        try:
            project = await self.json_request("GET", "/project/current")
            root = self._extract_path(project)
            if root:
                return normalize_path(root)
        except HTTPException:
            pass
        try:
            path_info = await self.json_request("GET", "/path")
        except HTTPException:
            return None
        root = self._extract_path(path_info)
        return normalize_path(root) if root else None

    async def list_sessions(self) -> List[Dict[str, Any]]:
        return await self.json_request("GET", "/session")

    async def get_session(self, session_id: str) -> Dict[str, Any]:
        return await self.json_request("GET", "/session/{session_id}".format(session_id=session_id))

    async def create_session(self, title: str) -> Dict[str, Any]:
        return await self.json_request("POST", "/session", json_body={"title": title})

    async def get_status_map(self) -> Dict[str, Any]:
        return await self.json_request("GET", "/session/status")

    async def session_messages(self, session_id: str, limit: int = 6) -> List[Dict[str, Any]]:
        return await self.json_request(
            "GET",
            "/session/{session_id}/message".format(session_id=session_id),
            params={"limit": limit},
        )

    async def session_todo(self, session_id: str) -> List[Dict[str, Any]]:
        return await self.json_request("GET", "/session/{session_id}/todo".format(session_id=session_id))

    async def session_diff(self, session_id: str, message_id: Optional[str] = None) -> List[Dict[str, Any]]:
        params = {"messageID": message_id} if message_id else None
        return await self.json_request(
            "GET",
            "/session/{session_id}/diff".format(session_id=session_id),
            params=params,
        )

    async def prompt_sync(self, session_id: str, body: Dict[str, Any], timeout_seconds: float) -> Dict[str, Any]:
        timeout = httpx.Timeout(
            connect=SETTINGS.connect_timeout_s,
            read=timeout_seconds,
            write=SETTINGS.write_timeout_s,
            pool=SETTINGS.pool_timeout_s,
        )
        return await self.json_request(
            "POST",
            "/session/{session_id}/message".format(session_id=session_id),
            json_body=body,
            timeout=timeout,
        )

    async def prompt_async(self, session_id: str, body: Dict[str, Any]) -> None:
        await self.json_request(
            "POST",
            "/session/{session_id}/prompt_async".format(session_id=session_id),
            json_body=body,
            expected_status=204,
        )

    async def reply_permission(self, session_id: str, permission_id: str, response: str) -> bool:
        remember = response == "always"
        result = await self.json_request(
            "POST",
            "/session/{session_id}/permissions/{permission_id}".format(
                session_id=session_id,
                permission_id=permission_id,
            ),
            json_body={"response": response, "remember": remember},
        )
        return bool(result)

    async def abort(self, session_id: str) -> bool:
        result = await self.json_request(
            "POST",
            "/session/{session_id}/abort".format(session_id=session_id),
        )
        return bool(result)

    async def fork(self, session_id: str, message_id: Optional[str] = None) -> Dict[str, Any]:
        body = {"messageID": message_id} if message_id else {}
        return await self.json_request(
            "POST",
            "/session/{session_id}/fork".format(session_id=session_id),
            json_body=body,
        )


opencode = OpenCodeHTTP()


class SessionLockRegistry:
    def __init__(self) -> None:
        self._guard = asyncio.Lock()
        self._locks: Dict[str, asyncio.Lock] = {}

    async def get(self, session_id: str) -> asyncio.Lock:
        async with self._guard:
            if session_id not in self._locks:
                self._locks[session_id] = asyncio.Lock()
            return self._locks[session_id]


session_locks = SessionLockRegistry()


class EventState:
    def __init__(self) -> None:
        self.connected = False
        self.last_error: Optional[str] = None
        self.last_event_at: Optional[int] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "connected": self.connected,
            "last_error": self.last_error,
            "last_event_at": self.last_event_at,
        }


event_state = EventState()


async def iter_sse_json(response: httpx.Response):
    data_lines: List[str] = []
    async for line in response.aiter_lines():
        if line == "":
            if data_lines:
                payload = "\n".join(data_lines)
                data_lines = []
                try:
                    yield json.loads(payload)
                except Exception:
                    log.warning("Failed to parse SSE payload: %s", payload[:500])
            continue
        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        if value.startswith(" "):
            value = value[1:]
        if field == "data":
            data_lines.append(value)


async def opencode_event_listener(stop_event: asyncio.Event) -> None:
    if not SETTINGS.event_listener_enabled:
        return

    backoff = SETTINGS.event_reconnect_min_s
    auth = opencode_auth()

    while not stop_event.is_set():
        try:
            async with httpx.AsyncClient(
                base_url=SETTINGS.opencode_base_url,
                auth=auth,
                timeout=httpx.Timeout(connect=SETTINGS.connect_timeout_s, read=None, write=None, pool=None),
            ) as client:
                async with client.stream("GET", "/global/event", headers={"Accept": "text/event-stream"}) as response:
                    response.raise_for_status()
                    event_state.connected = True
                    event_state.last_error = None
                    backoff = SETTINGS.event_reconnect_min_s

                    async for envelope in iter_sse_json(response):
                        if stop_event.is_set():
                            return
                        event_state.last_event_at = now_ms()
                        payload = envelope.get("payload", envelope)
                        event_type = payload.get("type")
                        props = payload.get("properties", {})
                        if event_type == "permission.updated":
                            db.upsert_permission(props)
                        elif event_type == "permission.replied":
                            permission_id = props.get("permissionID")
                            response_value = props.get("response", "")
                            if permission_id:
                                db.mark_permission_replied(permission_id, str(response_value))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            event_state.connected = False
            event_state.last_error = str(exc)
            log.warning("OpenCode SSE listener disconnected: %s", exc)

        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, SETTINGS.event_reconnect_max_s)


async def get_fixed_workspace_root(requested_workspace_root: Optional[str] = None) -> str:
    global _FIXED_WORKSPACE_ROOT_CACHE

    requested = normalize_path(requested_workspace_root) if requested_workspace_root else None
    if _FIXED_WORKSPACE_ROOT_CACHE is None:
        async with _FIXED_WORKSPACE_ROOT_LOCK:
            if _FIXED_WORKSPACE_ROOT_CACHE is None:
                resolved = await opencode.current_project_root()
                if not resolved:
                    raise HTTPException(
                        status_code=502,
                        detail="Could not determine fixed workspace root from OpenCode /project/current or /path.",
                    )
                _FIXED_WORKSPACE_ROOT_CACHE = normalize_path(resolved)

    fixed_root = _FIXED_WORKSPACE_ROOT_CACHE
    if requested and requested != fixed_root:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "workspace_root must match the current OpenCode project root.",
                "fixed_workspace_root": fixed_root,
                "requested_workspace_root": requested,
            },
        )
    return fixed_root


async def refresh_existing_binding(ctx: RequestContext, row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    try:
        remote = await opencode.get_session(row["opencode_session_id"])
    except HTTPException:
        db.archive_binding(ctx, row["session_ref"])
        return None

    db.touch_binding(
        row["session_ref"],
        preferred_agent=row.get("preferred_agent"),
        title=remote.get("title") or row["title"],
    )
    return db.get_binding_by_ref(ctx, row["session_ref"]) or row


async def find_binding(
    ctx: RequestContext,
    session_ref: Optional[str],
    workspace_root: Optional[str],
    slot: str,
    validate_remote: bool = False,
) -> Tuple[Optional[Dict[str, Any]], str]:
    fixed_root = await get_fixed_workspace_root(workspace_root)
    row: Optional[Dict[str, Any]]
    if session_ref:
        row = db.get_binding_by_ref(ctx, session_ref)
        if row and row["workspace_root"] != fixed_root:
            raise HTTPException(
                status_code=400,
                detail="session_ref does not belong to the current fixed workspace root.",
            )
    else:
        row = db.get_binding_for_chat(ctx, fixed_root, slot)

    if row and validate_remote:
        row = await refresh_existing_binding(ctx, row)
    return row, fixed_root


async def ensure_binding(
    ctx: RequestContext,
    workspace_root: Optional[str],
    slot: str,
    title: Optional[str],
    preferred_agent: Optional[str],
    create_if_missing: bool,
) -> Tuple[Dict[str, Any], bool, str]:
    existing, fixed_root = await find_binding(
        ctx,
        session_ref=None,
        workspace_root=workspace_root,
        slot=slot,
        validate_remote=True,
    )
    if existing:
        return existing, False, fixed_root

    if not create_if_missing:
        raise HTTPException(status_code=404, detail="No active binding for this chat and slot.")

    new_title = compact_title(fixed_root, slot, title)
    session = await opencode.create_session(new_title)
    session_ref = make_session_ref(ctx.user_id, ctx.chat_id, fixed_root, slot)
    db.archive_active_binding(ctx, fixed_root, slot)
    row = db.upsert_binding(
        session_ref=session_ref,
        ctx=ctx,
        workspace_root=fixed_root,
        slot=slot,
        opencode_session_id=session["id"],
        title=session.get("title") or new_title,
        preferred_agent=preferred_agent,
        archived=False,
    )
    return row, True, fixed_root


async def resolve_task_binding(request: Request, body: TaskRequest) -> Tuple[RequestContext, Dict[str, Any], bool]:
    ctx = request_to_ctx(request)
    if body.session_ref:
        row, _ = await find_binding(
            ctx,
            session_ref=body.session_ref,
            workspace_root=body.workspace_root,
            slot=body.slot,
            validate_remote=True,
        )
        if not row:
            raise HTTPException(status_code=404, detail="Unknown session_ref: {ref}".format(ref=body.session_ref))
        return ctx, row, False
    row, created, _ = await ensure_binding(
        ctx=ctx,
        workspace_root=body.workspace_root,
        slot=body.slot,
        title=body.title,
        preferred_agent=body.agent,
        create_if_missing=body.create_if_missing,
    )
    return ctx, row, created


def build_prompt_body(req: TaskRequest) -> Dict[str, Any]:
    body: Dict[str, Any] = {
        "parts": [{"type": "text", "text": req.prompt}],
        "system": JSON_ONLY_SYSTEM_PROMPT,
    }
    if req.agent:
        body["agent"] = req.agent
    if req.model:
        model_payload = req.model.to_opencode()
        if model_payload:
            body["model"] = model_payload
    if SETTINGS.use_structured_output:
        body["format"] = {
            "type": "json_schema",
            "schema": RESULT_SCHEMA,
            "retryCount": 1,
        }
    return body


async def inject_briefing(session_id: str, briefing: str, agent: Optional[str], model: Optional[ModelSelector]) -> None:
    if not briefing.strip():
        return
    body: Dict[str, Any] = {
        "noReply": True,
        "parts": [{"type": "text", "text": "[OpenWebUI handoff]\n{briefing}".format(briefing=briefing.strip())}],
    }
    if agent:
        body["agent"] = agent
    if model:
        model_payload = model.to_opencode()
        if model_payload:
            body["model"] = model_payload
    await opencode.prompt_sync(session_id, body, timeout_seconds=min(SETTINGS.sync_wait_timeout_s, 30.0))


def empty_diff() -> Dict[str, Any]:
    return {
        "files_changed": [],
        "summary": [],
        "totals": {"files": 0, "additions": 0, "deletions": 0},
    }


def not_started_status_payload(fixed_root: str, slot: str) -> Dict[str, Any]:
    return {
        "started": False,
        "fixed_workspace_root": fixed_root,
        "slot": slot,
        "binding": None,
        "remote_session": None,
        "event_listener": event_state.as_dict(),
        "status": {"type": "not_started"},
        "needs_approval": False,
        "pending_permissions": [],
        "latest_result": None,
        "latest_raw_text": "",
        "latest_commands_run": [],
        "messages_seen": 0,
        "diff": empty_diff(),
        "todos": [],
    }


async def collect_status_payload(
    session_ref: str,
    opencode_session_id: str,
    include_diff: bool,
    include_todos: bool,
    max_messages: int,
) -> Dict[str, Any]:
    status_map = await opencode.get_status_map()
    status = status_map.get(opencode_session_id, {"type": "unknown"})

    messages = await opencode.session_messages(opencode_session_id, limit=max_messages)
    last_assistant: Optional[Dict[str, Any]] = None
    for message in reversed(messages):
        if (message.get("info") or {}).get("role") == "assistant":
            last_assistant = message
            break

    raw_text = parts_to_text(last_assistant.get("parts", []) if last_assistant else [])
    latest_result = normalize_result_json(parse_jsonish(raw_text), raw_text) if raw_text else None
    commands_run = parts_to_commands(last_assistant.get("parts", []) if last_assistant else [])

    diff_items: List[Dict[str, Any]] = []
    if include_diff:
        try:
            diff_items = await opencode.session_diff(opencode_session_id)
        except HTTPException as exc:
            log.warning("diff fetch failed for %s: %s", opencode_session_id, exc.detail)

    todos: List[Dict[str, Any]] = []
    if include_todos:
        try:
            todos = await opencode.session_todo(opencode_session_id)
        except HTTPException as exc:
            log.warning("todo fetch failed for %s: %s", opencode_session_id, exc.detail)

    pending_permissions = db.list_permissions(session_ref=session_ref, state="pending")
    return {
        "status": status,
        "needs_approval": bool(pending_permissions),
        "pending_permissions": pending_permissions,
        "latest_result": latest_result,
        "latest_raw_text": raw_text,
        "latest_commands_run": commands_run,
        "messages_seen": len(messages),
        "diff": {
            "files_changed": [item.get("file") for item in diff_items if item.get("file")],
            "summary": diff_items,
            "totals": {
                "files": len(diff_items),
                "additions": sum(int(item.get("additions", 0) or 0) for item in diff_items),
                "deletions": sum(int(item.get("deletions", 0) or 0) for item in diff_items),
            },
        },
        "todos": todos,
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    stop_event = asyncio.Event()
    listener_task = None
    if SETTINGS.event_listener_enabled:
        listener_task = asyncio.create_task(opencode_event_listener(stop_event))
    try:
        yield
    finally:
        stop_event.set()
        if listener_task:
            listener_task.cancel()
            try:
                await listener_task
            except asyncio.CancelledError:
                pass
        await opencode.close()


app = FastAPI(
    title=SETTINGS.app_name,
    version="1.1.0",
    description="Local-only OpenCode wrapper for OpenWebUI.",
    lifespan=lifespan,
)


@app.get("/health", operation_id="oc_health", tags=["meta"])
async def health():
    remote = await opencode.health()
    fixed_root = await get_fixed_workspace_root()
    return {
        "ok": True,
        "orchestrator": {"db_path": os.path.abspath(SETTINGS.db_path)},
        "fixed_workspace_root": fixed_root,
        "opencode": remote,
        "event_listener": event_state.as_dict(),
    }


@app.get("/oc/session/list", operation_id="oc_session_list", tags=["session"])
async def oc_session_list(
    request: Request,
    workspace_root: Optional[str] = Query(default=None),
    include_archived: bool = Query(default=False),
    include_remote_unbound: bool = Query(default=False),
):
    ctx = request_to_ctx(request)
    fixed_root = await get_fixed_workspace_root(workspace_root)
    rows = db.list_bindings_for_chat(ctx, workspace_root=fixed_root, include_archived=include_archived)
    bindings = [binding_to_out(row) for row in rows]

    remote_unbound: List[Dict[str, Any]] = []
    if include_remote_unbound:
        remote_sessions = await opencode.list_sessions()
        bound_ids = {row["opencode_session_id"] for row in rows}
        for item in remote_sessions:
            directory = item.get("directory")
            if not directory or normalize_path(directory) != fixed_root:
                continue
            if item.get("id") in bound_ids:
                continue
            remote_unbound.append(
                {
                    "opencode_session_id": item.get("id"),
                    "title": item.get("title", ""),
                    "directory": directory,
                    "created_at": (item.get("time") or {}).get("created"),
                    "updated_at": (item.get("time") or {}).get("updated"),
                }
            )

    return {
        "fixed_workspace_root": fixed_root,
        "active_bindings": bindings,
        "remote_unbound_sessions": remote_unbound,
    }


@app.post("/oc/session/ensure", operation_id="oc_session_ensure", tags=["session"])
async def oc_session_ensure(request: Request, body: SessionEnsureRequest):
    ctx = request_to_ctx(request)
    row, created, fixed_root = await ensure_binding(
        ctx=ctx,
        workspace_root=body.workspace_root,
        slot=body.slot,
        title=body.title,
        preferred_agent=body.preferred_agent,
        create_if_missing=body.auto_create,
    )
    return {
        "created": created,
        "fixed_workspace_root": fixed_root,
        "binding": binding_to_out(row),
    }


@app.post("/oc/session/attach", operation_id="oc_session_attach", tags=["session"])
async def oc_session_attach(request: Request, body: SessionAttachRequest):
    ctx = request_to_ctx(request)
    fixed_root = await get_fixed_workspace_root(body.workspace_root)
    remote = await opencode.get_session(body.opencode_session_id)
    directory = remote.get("directory")
    if directory and normalize_path(directory) != fixed_root:
        raise HTTPException(
            status_code=400,
            detail="The requested OpenCode session belongs to a different workspace root.",
        )

    slot = body.slot.strip() or "default"
    title = body.title or remote.get("title") or compact_title(fixed_root, slot, None)
    if body.archive_existing:
        db.archive_active_binding(ctx, fixed_root, slot)

    session_ref = make_session_ref(ctx.user_id, ctx.chat_id, fixed_root, slot)
    row = db.upsert_binding(
        session_ref=session_ref,
        ctx=ctx,
        workspace_root=fixed_root,
        slot=slot,
        opencode_session_id=body.opencode_session_id,
        title=title,
        preferred_agent=body.preferred_agent,
        archived=False,
    )
    return {"attached": True, "binding": binding_to_out(row)}


@app.post("/oc/session/archive", operation_id="oc_session_archive", tags=["session"])
async def oc_session_archive(request: Request, body: SessionArchiveRequest):
    ctx = request_to_ctx(request)
    archived = db.archive_binding(ctx, body.session_ref)
    if not archived:
        raise HTTPException(status_code=404, detail="Unknown session_ref: {ref}".format(ref=body.session_ref))
    return {"archived": True, "session_ref": body.session_ref}


@app.post("/oc/task/sync", operation_id="oc_task_sync", tags=["task"])
async def oc_task_sync(request: Request, body: TaskRequest):
    ctx, row, created = await resolve_task_binding(request, body)
    session_id = row["opencode_session_id"]
    session_ref = row["session_ref"]
    lock = await session_locks.get(session_id)

    async with lock:
        if body.briefing:
            await inject_briefing(session_id, body.briefing, body.agent, body.model)

        request_body = build_prompt_body(body)
        db.touch_binding(session_ref, preferred_agent=body.agent)
        try:
            result = await opencode.prompt_sync(
                session_id,
                request_body,
                timeout_seconds=SETTINGS.sync_wait_timeout_s,
            )
            info = result.get("info", {}) if isinstance(result, dict) else {}
            parts = result.get("parts", []) if isinstance(result, dict) else []
            raw_text = parts_to_text(parts)
            structured = None
            if isinstance(info, dict):
                structured = info.get("structured_output") or info.get("structuredOutput")
            parsed = normalize_result_json(
                structured if isinstance(structured, dict) else parse_jsonish(raw_text),
                raw_text,
            )
            status_payload = await collect_status_payload(
                session_ref=session_ref,
                opencode_session_id=session_id,
                include_diff=body.include_diff,
                include_todos=body.include_todos,
                max_messages=body.max_messages_for_status,
            )
            return {
                "started": True,
                "accepted": True,
                "completed": True,
                "created_session": created,
                "binding": binding_to_out(row),
                "message_info": info,
                "result": parsed,
                "raw_text": raw_text,
                "commands_run": parts_to_commands(parts),
                "status_snapshot": status_payload,
            }
        except httpx.TimeoutException:
            status_payload = await collect_status_payload(
                session_ref=session_ref,
                opencode_session_id=session_id,
                include_diff=body.include_diff,
                include_todos=body.include_todos,
                max_messages=body.max_messages_for_status,
            )
            return {
                "started": True,
                "accepted": True,
                "completed": False,
                "created_session": created,
                "binding": binding_to_out(row),
                "reason": "OpenCode did not finish within SYNC_WAIT_TIMEOUT_S.",
                "status_snapshot": status_payload,
            }


@app.post("/oc/task/async", operation_id="oc_task_async", tags=["task"])
async def oc_task_async(request: Request, body: TaskRequest):
    ctx, row, created = await resolve_task_binding(request, body)
    session_id = row["opencode_session_id"]
    session_ref = row["session_ref"]
    lock = await session_locks.get(session_id)

    async with lock:
        if body.briefing:
            await inject_briefing(session_id, body.briefing, body.agent, body.model)
        db.touch_binding(session_ref, preferred_agent=body.agent)
        await opencode.prompt_async(session_id, build_prompt_body(body))

    status_payload = await collect_status_payload(
        session_ref=session_ref,
        opencode_session_id=session_id,
        include_diff=body.include_diff,
        include_todos=body.include_todos,
        max_messages=body.max_messages_for_status,
    )
    return {
        "started": True,
        "accepted": True,
        "completed": False,
        "created_session": created,
        "binding": binding_to_out(row),
        "status_snapshot": status_payload,
    }


@app.post("/oc/status", operation_id="oc_status", tags=["task"])
async def oc_status(request: Request, body: StatusRequest):
    ctx = request_to_ctx(request)
    row, fixed_root = await find_binding(
        ctx,
        session_ref=body.session_ref,
        workspace_root=body.workspace_root,
        slot=body.slot,
        validate_remote=True,
    )
    if not row:
        return not_started_status_payload(fixed_root=fixed_root, slot=body.slot)

    try:
        remote = await opencode.get_session(row["opencode_session_id"])
    except HTTPException:
        db.archive_binding(ctx, row["session_ref"])
        return not_started_status_payload(fixed_root=fixed_root, slot=body.slot)

    payload = await collect_status_payload(
        session_ref=row["session_ref"],
        opencode_session_id=row["opencode_session_id"],
        include_diff=body.include_diff,
        include_todos=body.include_todos,
        max_messages=body.max_messages,
    )
    return {
        "started": True,
        "fixed_workspace_root": fixed_root,
        "slot": body.slot,
        "binding": binding_to_out(row),
        "remote_session": remote,
        "event_listener": event_state.as_dict(),
        **payload,
    }


@app.get("/oc/permission/list", operation_id="oc_permission_list", tags=["permission"])
async def oc_permission_list(
    request: Request,
    session_ref: Optional[str] = Query(default=None),
    workspace_root: Optional[str] = Query(default=None),
    slot: str = Query(default="default"),
):
    ctx = request_to_ctx(request)
    row, fixed_root = await find_binding(
        ctx,
        session_ref=session_ref,
        workspace_root=workspace_root,
        slot=slot,
        validate_remote=False,
    )
    if not row:
        return {
            "started": False,
            "fixed_workspace_root": fixed_root,
            "slot": slot,
            "binding": None,
            "pending_permissions": [],
            "count": 0,
        }

    permissions = db.list_permissions(session_ref=row["session_ref"], state="pending")
    return {
        "started": True,
        "fixed_workspace_root": fixed_root,
        "slot": slot,
        "binding": binding_to_out(row),
        "pending_permissions": permissions,
        "count": len(permissions),
    }


@app.post("/oc/permission/reply", operation_id="oc_permission_reply", tags=["permission"])
async def oc_permission_reply(request: Request, body: PermissionReplyRequest):
    ctx = request_to_ctx(request)
    row, _ = await find_binding(
        ctx,
        session_ref=body.session_ref,
        workspace_root=body.workspace_root,
        slot=body.slot,
        validate_remote=True,
    )
    if not row:
        raise HTTPException(status_code=404, detail="No active OpenCode session for this chat and slot.")

    session_id = row["opencode_session_id"]
    lock = await session_locks.get(session_id)
    async with lock:
        ok = await opencode.reply_permission(session_id, body.permission_id, body.response)

    if ok:
        db.mark_permission_replied(body.permission_id, body.response)

    payload = await collect_status_payload(
        session_ref=row["session_ref"],
        opencode_session_id=session_id,
        include_diff=True,
        include_todos=True,
        max_messages=6,
    )
    return {"ok": ok, "binding": binding_to_out(row), "status_snapshot": payload}


@app.post("/oc/abort", operation_id="oc_abort", tags=["task"])
async def oc_abort(request: Request, body: AbortRequest):
    ctx = request_to_ctx(request)
    row, _ = await find_binding(
        ctx,
        session_ref=body.session_ref,
        workspace_root=body.workspace_root,
        slot=body.slot,
        validate_remote=True,
    )
    if not row:
        raise HTTPException(status_code=404, detail="No active OpenCode session for this chat and slot.")

    session_id = row["opencode_session_id"]
    lock = await session_locks.get(session_id)
    async with lock:
        ok = await opencode.abort(session_id)

    payload = await collect_status_payload(
        session_ref=row["session_ref"],
        opencode_session_id=session_id,
        include_diff=True,
        include_todos=True,
        max_messages=6,
    )
    return {"ok": ok, "binding": binding_to_out(row), "status_snapshot": payload}


@app.post("/oc/fork", operation_id="oc_fork", tags=["session"])
async def oc_fork(request: Request, body: SessionForkRequest):
    ctx = request_to_ctx(request)
    row, fixed_root = await find_binding(
        ctx,
        session_ref=body.session_ref,
        workspace_root=body.workspace_root,
        slot=body.slot,
        validate_remote=True,
    )
    if not row:
        raise HTTPException(status_code=404, detail="No active OpenCode session for this chat and slot.")

    session_id = row["opencode_session_id"]
    lock = await session_locks.get(session_id)
    async with lock:
        forked = await opencode.fork(session_id, body.message_id)

    new_slot = body.new_slot.strip() or "experiment"
    new_title = body.title or "{title} ({slot})".format(title=row["title"], slot=new_slot)
    new_ref = make_session_ref(ctx.user_id, ctx.chat_id, fixed_root, new_slot)
    db.archive_active_binding(ctx, fixed_root, new_slot)
    new_row = db.upsert_binding(
        session_ref=new_ref,
        ctx=ctx,
        workspace_root=fixed_root,
        slot=new_slot,
        opencode_session_id=forked["id"],
        title=forked.get("title") or new_title,
        preferred_agent=row.get("preferred_agent"),
        archived=False,
    )
    return {"forked_from": binding_to_out(row), "forked_binding": binding_to_out(new_row)}


@app.post("/oc/diff", operation_id="oc_diff", tags=["task"])
async def oc_diff(request: Request, body: DiffRequest):
    ctx = request_to_ctx(request)
    row, _ = await find_binding(
        ctx,
        session_ref=body.session_ref,
        workspace_root=body.workspace_root,
        slot=body.slot,
        validate_remote=True,
    )
    if not row:
        raise HTTPException(status_code=404, detail="No active OpenCode session for this chat and slot.")

    diff_items = await opencode.session_diff(row["opencode_session_id"], body.message_id)
    return {
        "binding": binding_to_out(row),
        "files_changed": [item.get("file") for item in diff_items if item.get("file")],
        "totals": {
            "files": len(diff_items),
            "additions": sum(int(item.get("additions", 0) or 0) for item in diff_items),
            "deletions": sum(int(item.get("deletions", 0) or 0) for item in diff_items),
        },
        "diff": diff_items,
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app:app",
        host=os.getenv("ORCH_HOST", "127.0.0.1"),
        port=int(os.getenv("ORCH_PORT", "8787")),
        reload=False,
    )
