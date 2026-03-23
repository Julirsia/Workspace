import json
import os
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional, Protocol, Tuple


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


def now_ms() -> int:
    return int(time.time() * 1000)


class RequestContextLike(Protocol):
    user_id: str
    chat_id: str


class DB:
    def __init__(self, path: str, opencode_base_url: str):
        self.path = path
        self.opencode_base_url = opencode_base_url
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

    def archive_active_binding(self, ctx: RequestContextLike, workspace_root: str, slot: str) -> None:
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
        ctx: RequestContextLike,
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
                        self.opencode_base_url,
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

    def get_binding_by_ref(self, ctx: RequestContextLike, session_ref: str) -> Optional[Dict[str, Any]]:
        return self.fetchone(
            """
            SELECT * FROM session_binding
            WHERE session_ref=? AND owui_user_id=? AND archived=0
            """,
            (session_ref, ctx.user_id),
        )

    def get_binding_for_chat(self, ctx: RequestContextLike, workspace_root: str, slot: str) -> Optional[Dict[str, Any]]:
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
        ctx: RequestContextLike,
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

    def archive_binding(self, ctx: RequestContextLike, session_ref: str) -> bool:
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
