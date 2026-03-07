import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import httpx
from fastapi import HTTPException, Request

from .client import OpenCodeClient
from .config import Settings
from .hooks import OrchestratorHooks, parts_to_commands, parts_to_text
from .store import DB


log = logging.getLogger("opencode2openwebui")


def now_ms() -> int:
    return int(time.time() * 1000)


def normalize_path(path: str) -> str:
    return os.path.abspath(os.path.expanduser(path))


@dataclass(frozen=True)
class RequestContext:
    user_id: str
    chat_id: str
    message_id: Optional[str] = None


class SessionLockRegistry:
    def __init__(self) -> None:
        self._guard: Optional[asyncio.Lock] = None
        self._locks: Dict[str, asyncio.Lock] = {}

    async def get(self, session_id: str) -> asyncio.Lock:
        if self._guard is None:
            self._guard = asyncio.Lock()
        async with self._guard:
            if session_id not in self._locks:
                self._locks[session_id] = asyncio.Lock()
            return self._locks[session_id]


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


class OrchestratorService:
    def __init__(
        self,
        *,
        settings: Settings,
        db: DB,
        opencode: OpenCodeClient,
        hooks: OrchestratorHooks,
    ) -> None:
        self.settings = settings
        self.db = db
        self.opencode = opencode
        self.hooks = hooks
        self.session_locks = SessionLockRegistry()
        self.event_state = EventState()
        self._fixed_workspace_root_cache: Optional[str] = None
        self._fixed_workspace_root_lock: Optional[asyncio.Lock] = None
        self._stop_event: Optional[asyncio.Event] = None
        self._listener_task: Optional[asyncio.Task[None]] = None

    async def start(self) -> None:
        if not self.settings.event_listener_enabled or self._listener_task is not None:
            return
        self._stop_event = asyncio.Event()
        self._listener_task = asyncio.create_task(self._opencode_event_listener(self._stop_event))

    async def stop(self) -> None:
        stop_event = self._stop_event
        listener_task = self._listener_task
        self._stop_event = None
        self._listener_task = None

        if stop_event is not None:
            stop_event.set()
        if listener_task is not None:
            listener_task.cancel()
            try:
                await listener_task
            except asyncio.CancelledError:
                pass
        await self.opencode.close()

    def request_to_ctx(self, request: Request) -> RequestContext:
        if self.settings.orch_api_key:
            supplied = request.headers.get("x-api-key", "")
            if supplied != self.settings.orch_api_key:
                raise HTTPException(status_code=401, detail="Invalid API key")

        user_id = request.headers.get("X-OpenWebUI-User-Id")
        chat_id = request.headers.get("X-OpenWebUI-Chat-Id")
        message_id = request.headers.get("X-OpenWebUI-Message-Id")

        if not user_id or not chat_id:
            if not self.settings.allow_missing_owui_headers:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Missing Open WebUI forwarded headers. "
                        "Enable ENABLE_FORWARD_USER_INFO_HEADERS=true in Open WebUI."
                    ),
                )
            user_id = user_id or self.settings.default_user_id
            chat_id = chat_id or self.settings.default_chat_id

        return RequestContext(user_id=user_id, chat_id=chat_id, message_id=message_id)

    @staticmethod
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

    @staticmethod
    def empty_diff() -> Dict[str, Any]:
        return {
            "files_changed": [],
            "summary": [],
            "totals": {"files": 0, "additions": 0, "deletions": 0},
        }

    def not_started_status_payload(self, *, fixed_root: str, slot: str) -> Dict[str, Any]:
        return {
            "started": False,
            "fixed_workspace_root": fixed_root,
            "slot": slot,
            "binding": None,
            "remote_session": None,
            "event_listener": self.event_state.as_dict(),
            "status": {"type": "not_started"},
            "needs_approval": False,
            "pending_permissions": [],
            "latest_result": None,
            "latest_raw_text": "",
            "latest_commands_run": [],
            "messages_seen": 0,
            "diff": self.empty_diff(),
            "todos": [],
        }

    async def get_fixed_workspace_root(self, requested_workspace_root: Optional[str] = None) -> str:
        requested = normalize_path(requested_workspace_root) if requested_workspace_root else None
        if self._fixed_workspace_root_cache is None:
            if self._fixed_workspace_root_lock is None:
                self._fixed_workspace_root_lock = asyncio.Lock()
            async with self._fixed_workspace_root_lock:
                if self._fixed_workspace_root_cache is None:
                    resolved = await self.opencode.current_project_root()
                    if not resolved:
                        raise HTTPException(
                            status_code=502,
                            detail="Could not determine fixed workspace root from OpenCode /project/current or /path.",
                        )
                    self._fixed_workspace_root_cache = normalize_path(resolved)

        fixed_root = self._fixed_workspace_root_cache
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

    async def refresh_existing_binding(self, ctx: RequestContext, row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        try:
            remote = await self.opencode.get_session(row["opencode_session_id"])
        except HTTPException:
            self.db.archive_binding(ctx, row["session_ref"])
            return None

        self.db.touch_binding(
            row["session_ref"],
            preferred_agent=row.get("preferred_agent"),
            title=remote.get("title") or row["title"],
        )
        return self.db.get_binding_by_ref(ctx, row["session_ref"]) or row

    async def find_binding(
        self,
        ctx: RequestContext,
        *,
        session_ref: Optional[str],
        workspace_root: Optional[str],
        slot: str,
        validate_remote: bool = False,
    ) -> Tuple[Optional[Dict[str, Any]], str]:
        fixed_root = await self.get_fixed_workspace_root(workspace_root)
        row: Optional[Dict[str, Any]]
        if session_ref:
            row = self.db.get_binding_by_ref(ctx, session_ref)
            if row and row["workspace_root"] != fixed_root:
                raise HTTPException(
                    status_code=400,
                    detail="session_ref does not belong to the current fixed workspace root.",
                )
        else:
            row = self.db.get_binding_for_chat(ctx, fixed_root, slot)

        if row and validate_remote:
            row = await self.refresh_existing_binding(ctx, row)
        return row, fixed_root

    async def ensure_binding(
        self,
        ctx: RequestContext,
        *,
        workspace_root: Optional[str],
        slot: str,
        title: Optional[str],
        preferred_agent: Optional[str],
        create_if_missing: bool,
    ) -> Tuple[Dict[str, Any], bool, str]:
        existing, fixed_root = await self.find_binding(
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

        new_title = self.hooks.make_session_title(
            workspace_root=fixed_root,
            slot=slot,
            explicit_title=title,
        )
        session = await self.opencode.create_session(new_title)
        session_ref = self.hooks.make_session_ref(
            opencode_base_url=self.settings.opencode_base_url,
            user_id=ctx.user_id,
            chat_id=ctx.chat_id,
            workspace_root=fixed_root,
            slot=slot,
        )
        self.db.archive_active_binding(ctx, fixed_root, slot)
        row = self.db.upsert_binding(
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

    async def resolve_task_binding(
        self,
        request: Request,
        *,
        session_ref: Optional[str],
        workspace_root: Optional[str],
        slot: str,
        title: Optional[str],
        agent: Optional[str],
        create_if_missing: bool,
    ) -> Tuple[RequestContext, Dict[str, Any], bool]:
        ctx = self.request_to_ctx(request)
        if session_ref:
            row, _ = await self.find_binding(
                ctx,
                session_ref=session_ref,
                workspace_root=workspace_root,
                slot=slot,
                validate_remote=True,
            )
            if not row:
                raise HTTPException(status_code=404, detail="Unknown session_ref: {ref}".format(ref=session_ref))
            return ctx, row, False
        row, created, _ = await self.ensure_binding(
            ctx,
            workspace_root=workspace_root,
            slot=slot,
            title=title,
            preferred_agent=agent,
            create_if_missing=create_if_missing,
        )
        return ctx, row, created

    async def inject_briefing(
        self,
        *,
        session_id: str,
        briefing: str,
        agent: Optional[str],
        model_payload: Optional[Dict[str, str]],
    ) -> None:
        body = self.hooks.build_briefing_body(
            briefing=briefing,
            agent=agent,
            model_payload=model_payload,
        )
        if body is None:
            return
        await self.opencode.prompt_sync(
            session_id,
            body,
            timeout_seconds=min(self.settings.sync_wait_timeout_s, 30.0),
        )

    async def collect_status_payload(
        self,
        *,
        session_ref: str,
        opencode_session_id: str,
        include_diff: bool,
        include_todos: bool,
        max_messages: int,
    ) -> Dict[str, Any]:
        status_map = await self.opencode.get_status_map()
        status = status_map.get(opencode_session_id, {"type": "unknown"})

        messages = await self.opencode.session_messages(opencode_session_id, limit=max_messages)
        last_assistant: Optional[Dict[str, Any]] = None
        for message in reversed(messages):
            if (message.get("info") or {}).get("role") == "assistant":
                last_assistant = message
                break

        raw_text = parts_to_text(last_assistant.get("parts", []) if last_assistant else [])
        latest_result = (
            self.hooks.normalize_result(raw_text=raw_text, structured_output=None)
            if raw_text
            else None
        )
        commands_run = parts_to_commands(last_assistant.get("parts", []) if last_assistant else [])

        diff_items: List[Dict[str, Any]] = []
        if include_diff:
            try:
                diff_items = await self.opencode.session_diff(opencode_session_id)
            except HTTPException as exc:
                log.warning("diff fetch failed for %s: %s", opencode_session_id, exc.detail)

        todos: List[Dict[str, Any]] = []
        if include_todos:
            try:
                todos = await self.opencode.session_todo(opencode_session_id)
            except HTTPException as exc:
                log.warning("todo fetch failed for %s: %s", opencode_session_id, exc.detail)

        pending_permissions = self.db.list_permissions(session_ref=session_ref, state="pending")
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

    async def health_payload(self) -> Dict[str, Any]:
        remote = await self.opencode.health()
        fixed_root = await self.get_fixed_workspace_root()
        return {
            "ok": True,
            "orchestrator": {"db_path": os.path.abspath(self.settings.db_path)},
            "fixed_workspace_root": fixed_root,
            "opencode": remote,
            "event_listener": self.event_state.as_dict(),
        }

    async def list_sessions(
        self,
        request: Request,
        *,
        workspace_root: Optional[str],
        include_archived: bool,
        include_remote_unbound: bool,
    ) -> Dict[str, Any]:
        ctx = self.request_to_ctx(request)
        fixed_root = await self.get_fixed_workspace_root(workspace_root)
        rows = self.db.list_bindings_for_chat(ctx, workspace_root=fixed_root, include_archived=include_archived)
        bindings = [self.binding_to_out(row) for row in rows]

        remote_unbound: List[Dict[str, Any]] = []
        if include_remote_unbound:
            remote_sessions = await self.opencode.list_sessions()
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

    async def session_ensure(
        self,
        request: Request,
        *,
        workspace_root: Optional[str],
        slot: str,
        title: Optional[str],
        preferred_agent: Optional[str],
        auto_create: bool,
    ) -> Dict[str, Any]:
        ctx = self.request_to_ctx(request)
        row, created, fixed_root = await self.ensure_binding(
            ctx,
            workspace_root=workspace_root,
            slot=slot,
            title=title,
            preferred_agent=preferred_agent,
            create_if_missing=auto_create,
        )
        return {
            "created": created,
            "fixed_workspace_root": fixed_root,
            "binding": self.binding_to_out(row),
        }

    async def session_attach(
        self,
        request: Request,
        *,
        opencode_session_id: str,
        workspace_root: Optional[str],
        slot: str,
        title: Optional[str],
        preferred_agent: Optional[str],
        archive_existing: bool,
    ) -> Dict[str, Any]:
        ctx = self.request_to_ctx(request)
        fixed_root = await self.get_fixed_workspace_root(workspace_root)
        remote = await self.opencode.get_session(opencode_session_id)
        directory = remote.get("directory")
        if directory and normalize_path(directory) != fixed_root:
            raise HTTPException(
                status_code=400,
                detail="The requested OpenCode session belongs to a different workspace root.",
            )

        normalized_slot = slot.strip() or "default"
        resolved_title = title or remote.get("title") or self.hooks.make_session_title(
            workspace_root=fixed_root,
            slot=normalized_slot,
            explicit_title=None,
        )
        if archive_existing:
            self.db.archive_active_binding(ctx, fixed_root, normalized_slot)

        session_ref = self.hooks.make_session_ref(
            opencode_base_url=self.settings.opencode_base_url,
            user_id=ctx.user_id,
            chat_id=ctx.chat_id,
            workspace_root=fixed_root,
            slot=normalized_slot,
        )
        row = self.db.upsert_binding(
            session_ref=session_ref,
            ctx=ctx,
            workspace_root=fixed_root,
            slot=normalized_slot,
            opencode_session_id=opencode_session_id,
            title=resolved_title,
            preferred_agent=preferred_agent,
            archived=False,
        )
        return {"attached": True, "binding": self.binding_to_out(row)}

    async def session_archive(self, request: Request, *, session_ref: str) -> Dict[str, Any]:
        ctx = self.request_to_ctx(request)
        archived = self.db.archive_binding(ctx, session_ref)
        if not archived:
            raise HTTPException(status_code=404, detail="Unknown session_ref: {ref}".format(ref=session_ref))
        return {"archived": True, "session_ref": session_ref}

    async def task_sync(
        self,
        request: Request,
        *,
        session_ref: Optional[str],
        workspace_root: Optional[str],
        slot: str,
        title: Optional[str],
        prompt: str,
        briefing: Optional[str],
        agent: Optional[str],
        model_payload: Optional[Dict[str, str]],
        create_if_missing: bool,
        include_diff: bool,
        include_todos: bool,
        max_messages_for_status: int,
    ) -> Dict[str, Any]:
        _ctx, row, created = await self.resolve_task_binding(
            request,
            session_ref=session_ref,
            workspace_root=workspace_root,
            slot=slot,
            title=title,
            agent=agent,
            create_if_missing=create_if_missing,
        )
        session_id = row["opencode_session_id"]
        resolved_session_ref = row["session_ref"]
        lock = await self.session_locks.get(session_id)

        async with lock:
            if briefing:
                await self.inject_briefing(
                    session_id=session_id,
                    briefing=briefing,
                    agent=agent,
                    model_payload=model_payload,
                )

            request_body = self.hooks.build_prompt_body(
                prompt=prompt,
                agent=agent,
                model_payload=model_payload,
                use_structured_output=self.settings.use_structured_output,
            )
            self.db.touch_binding(resolved_session_ref, preferred_agent=agent)
            try:
                result = await self.opencode.prompt_sync(
                    session_id,
                    request_body,
                    timeout_seconds=self.settings.sync_wait_timeout_s,
                )
                info = result.get("info", {}) if isinstance(result, dict) else {}
                parts = result.get("parts", []) if isinstance(result, dict) else []
                raw_text = parts_to_text(parts)
                structured = None
                if isinstance(info, dict):
                    structured = info.get("structured_output") or info.get("structuredOutput")
                parsed = self.hooks.normalize_result(
                    raw_text=raw_text,
                    structured_output=structured if isinstance(structured, dict) else None,
                )
                status_payload = await self.collect_status_payload(
                    session_ref=resolved_session_ref,
                    opencode_session_id=session_id,
                    include_diff=include_diff,
                    include_todos=include_todos,
                    max_messages=max_messages_for_status,
                )
                return {
                    "started": True,
                    "accepted": True,
                    "completed": True,
                    "created_session": created,
                    "binding": self.binding_to_out(row),
                    "message_info": info,
                    "result": parsed,
                    "raw_text": raw_text,
                    "commands_run": parts_to_commands(parts),
                    "status_snapshot": status_payload,
                }
            except httpx.TimeoutException:
                status_payload = await self.collect_status_payload(
                    session_ref=resolved_session_ref,
                    opencode_session_id=session_id,
                    include_diff=include_diff,
                    include_todos=include_todos,
                    max_messages=max_messages_for_status,
                )
                return {
                    "started": True,
                    "accepted": True,
                    "completed": False,
                    "created_session": created,
                    "binding": self.binding_to_out(row),
                    "reason": "OpenCode did not finish within SYNC_WAIT_TIMEOUT_S.",
                    "status_snapshot": status_payload,
                }

    async def task_async(
        self,
        request: Request,
        *,
        session_ref: Optional[str],
        workspace_root: Optional[str],
        slot: str,
        title: Optional[str],
        prompt: str,
        briefing: Optional[str],
        agent: Optional[str],
        model_payload: Optional[Dict[str, str]],
        create_if_missing: bool,
        include_diff: bool,
        include_todos: bool,
        max_messages_for_status: int,
    ) -> Dict[str, Any]:
        _ctx, row, created = await self.resolve_task_binding(
            request,
            session_ref=session_ref,
            workspace_root=workspace_root,
            slot=slot,
            title=title,
            agent=agent,
            create_if_missing=create_if_missing,
        )
        session_id = row["opencode_session_id"]
        resolved_session_ref = row["session_ref"]
        lock = await self.session_locks.get(session_id)

        async with lock:
            if briefing:
                await self.inject_briefing(
                    session_id=session_id,
                    briefing=briefing,
                    agent=agent,
                    model_payload=model_payload,
                )
            self.db.touch_binding(resolved_session_ref, preferred_agent=agent)
            await self.opencode.prompt_async(
                session_id,
                self.hooks.build_prompt_body(
                    prompt=prompt,
                    agent=agent,
                    model_payload=model_payload,
                    use_structured_output=self.settings.use_structured_output,
                ),
            )

        status_payload = await self.collect_status_payload(
            session_ref=resolved_session_ref,
            opencode_session_id=session_id,
            include_diff=include_diff,
            include_todos=include_todos,
            max_messages=max_messages_for_status,
        )
        return {
            "started": True,
            "accepted": True,
            "completed": False,
            "created_session": created,
            "binding": self.binding_to_out(row),
            "status_snapshot": status_payload,
        }

    async def status(
        self,
        request: Request,
        *,
        session_ref: Optional[str],
        workspace_root: Optional[str],
        slot: str,
        include_diff: bool,
        include_todos: bool,
        max_messages: int,
    ) -> Dict[str, Any]:
        ctx = self.request_to_ctx(request)
        row, fixed_root = await self.find_binding(
            ctx,
            session_ref=session_ref,
            workspace_root=workspace_root,
            slot=slot,
            validate_remote=True,
        )
        if not row:
            return self.not_started_status_payload(fixed_root=fixed_root, slot=slot)

        try:
            remote = await self.opencode.get_session(row["opencode_session_id"])
        except HTTPException:
            self.db.archive_binding(ctx, row["session_ref"])
            return self.not_started_status_payload(fixed_root=fixed_root, slot=slot)

        payload = await self.collect_status_payload(
            session_ref=row["session_ref"],
            opencode_session_id=row["opencode_session_id"],
            include_diff=include_diff,
            include_todos=include_todos,
            max_messages=max_messages,
        )
        return {
            "started": True,
            "fixed_workspace_root": fixed_root,
            "slot": slot,
            "binding": self.binding_to_out(row),
            "remote_session": remote,
            "event_listener": self.event_state.as_dict(),
            **payload,
        }

    async def permission_list(
        self,
        request: Request,
        *,
        session_ref: Optional[str],
        workspace_root: Optional[str],
        slot: str,
    ) -> Dict[str, Any]:
        ctx = self.request_to_ctx(request)
        row, fixed_root = await self.find_binding(
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

        permissions = self.db.list_permissions(session_ref=row["session_ref"], state="pending")
        return {
            "started": True,
            "fixed_workspace_root": fixed_root,
            "slot": slot,
            "binding": self.binding_to_out(row),
            "pending_permissions": permissions,
            "count": len(permissions),
        }

    async def permission_reply(
        self,
        request: Request,
        *,
        session_ref: Optional[str],
        workspace_root: Optional[str],
        slot: str,
        permission_id: str,
        response: str,
    ) -> Dict[str, Any]:
        ctx = self.request_to_ctx(request)
        row, _ = await self.find_binding(
            ctx,
            session_ref=session_ref,
            workspace_root=workspace_root,
            slot=slot,
            validate_remote=True,
        )
        if not row:
            raise HTTPException(status_code=404, detail="No active OpenCode session for this chat and slot.")

        session_id = row["opencode_session_id"]
        lock = await self.session_locks.get(session_id)
        async with lock:
            ok = await self.opencode.reply_permission(session_id, permission_id, response)

        if ok:
            self.db.mark_permission_replied(permission_id, response)

        payload = await self.collect_status_payload(
            session_ref=row["session_ref"],
            opencode_session_id=session_id,
            include_diff=True,
            include_todos=True,
            max_messages=6,
        )
        return {"ok": ok, "binding": self.binding_to_out(row), "status_snapshot": payload}

    async def abort(
        self,
        request: Request,
        *,
        session_ref: Optional[str],
        workspace_root: Optional[str],
        slot: str,
    ) -> Dict[str, Any]:
        ctx = self.request_to_ctx(request)
        row, _ = await self.find_binding(
            ctx,
            session_ref=session_ref,
            workspace_root=workspace_root,
            slot=slot,
            validate_remote=True,
        )
        if not row:
            raise HTTPException(status_code=404, detail="No active OpenCode session for this chat and slot.")

        session_id = row["opencode_session_id"]
        lock = await self.session_locks.get(session_id)
        async with lock:
            ok = await self.opencode.abort(session_id)

        payload = await self.collect_status_payload(
            session_ref=row["session_ref"],
            opencode_session_id=session_id,
            include_diff=True,
            include_todos=True,
            max_messages=6,
        )
        return {"ok": ok, "binding": self.binding_to_out(row), "status_snapshot": payload}

    async def fork(
        self,
        request: Request,
        *,
        session_ref: Optional[str],
        workspace_root: Optional[str],
        slot: str,
        new_slot: str,
        title: Optional[str],
        message_id: Optional[str],
    ) -> Dict[str, Any]:
        ctx = self.request_to_ctx(request)
        row, fixed_root = await self.find_binding(
            ctx,
            session_ref=session_ref,
            workspace_root=workspace_root,
            slot=slot,
            validate_remote=True,
        )
        if not row:
            raise HTTPException(status_code=404, detail="No active OpenCode session for this chat and slot.")

        session_id = row["opencode_session_id"]
        lock = await self.session_locks.get(session_id)
        async with lock:
            forked = await self.opencode.fork(session_id, message_id)

        normalized_new_slot = new_slot.strip() or "experiment"
        new_title = title or "{title} ({slot})".format(title=row["title"], slot=normalized_new_slot)
        new_ref = self.hooks.make_session_ref(
            opencode_base_url=self.settings.opencode_base_url,
            user_id=ctx.user_id,
            chat_id=ctx.chat_id,
            workspace_root=fixed_root,
            slot=normalized_new_slot,
        )
        self.db.archive_active_binding(ctx, fixed_root, normalized_new_slot)
        new_row = self.db.upsert_binding(
            session_ref=new_ref,
            ctx=ctx,
            workspace_root=fixed_root,
            slot=normalized_new_slot,
            opencode_session_id=forked["id"],
            title=forked.get("title") or new_title,
            preferred_agent=row.get("preferred_agent"),
            archived=False,
        )
        return {"forked_from": self.binding_to_out(row), "forked_binding": self.binding_to_out(new_row)}

    async def diff(
        self,
        request: Request,
        *,
        session_ref: Optional[str],
        workspace_root: Optional[str],
        slot: str,
        message_id: Optional[str],
    ) -> Dict[str, Any]:
        ctx = self.request_to_ctx(request)
        row, _ = await self.find_binding(
            ctx,
            session_ref=session_ref,
            workspace_root=workspace_root,
            slot=slot,
            validate_remote=True,
        )
        if not row:
            raise HTTPException(status_code=404, detail="No active OpenCode session for this chat and slot.")

        diff_items = await self.opencode.session_diff(row["opencode_session_id"], message_id)
        return {
            "binding": self.binding_to_out(row),
            "files_changed": [item.get("file") for item in diff_items if item.get("file")],
            "totals": {
                "files": len(diff_items),
                "additions": sum(int(item.get("additions", 0) or 0) for item in diff_items),
                "deletions": sum(int(item.get("deletions", 0) or 0) for item in diff_items),
            },
            "diff": diff_items,
        }

    async def iter_sse_json(self, response: httpx.Response):
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

    async def _opencode_event_listener(self, stop_event: asyncio.Event) -> None:
        backoff = self.settings.event_reconnect_min_s
        auth = None
        if self.settings.opencode_password:
            auth = httpx.BasicAuth(self.settings.opencode_username, self.settings.opencode_password)

        while not stop_event.is_set():
            try:
                async with httpx.AsyncClient(
                    base_url=self.settings.opencode_base_url,
                    auth=auth,
                    timeout=httpx.Timeout(connect=self.settings.connect_timeout_s, read=None, write=None, pool=None),
                ) as client:
                    async with client.stream("GET", "/global/event", headers={"Accept": "text/event-stream"}) as response:
                        response.raise_for_status()
                        self.event_state.connected = True
                        self.event_state.last_error = None
                        backoff = self.settings.event_reconnect_min_s

                        async for envelope in self.iter_sse_json(response):
                            if stop_event.is_set():
                                return
                            self.event_state.last_event_at = now_ms()
                            payload = envelope.get("payload", envelope)
                            event_type = payload.get("type")
                            props = payload.get("properties", {})
                            if event_type == "permission.updated":
                                self.db.upsert_permission(props)
                            elif event_type == "permission.replied":
                                permission_id = props.get("permissionID")
                                response_value = props.get("response", "")
                                if permission_id:
                                    self.db.mark_permission_replied(permission_id, str(response_value))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.event_state.connected = False
                self.event_state.last_error = str(exc)
                log.warning("OpenCode SSE listener disconnected: %s", exc)

            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, self.settings.event_reconnect_max_s)
