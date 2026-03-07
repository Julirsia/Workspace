import logging
from contextlib import asynccontextmanager
from typing import Dict, Literal, Optional

from fastapi import FastAPI, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from .client import OpenCodeClient, OpenCodeHTTP
from .config import Settings, load_settings_from_env, validate_local_base_url
from .hooks import DefaultOrchestratorHooks, OrchestratorHooks
from .service import OrchestratorService
from .store import DB


class ModelSelector(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    provider_id: Optional[str] = Field(default=None, alias="providerID")
    model_id: Optional[str] = Field(default=None, alias="modelID")

    def to_opencode(self) -> Optional[Dict[str, str]]:
        if not self.provider_id or not self.model_id:
            return None
        return {"providerID": self.provider_id, "modelID": self.model_id}


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


def create_app(
    *,
    settings: Optional[Settings] = None,
    opencode_client: Optional[OpenCodeClient] = None,
    hooks: Optional[OrchestratorHooks] = None,
) -> FastAPI:
    resolved_settings = settings or load_settings_from_env()
    validate_local_base_url(resolved_settings.opencode_base_url)
    logging.basicConfig(
        level=getattr(logging, resolved_settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )

    db = DB(resolved_settings.db_path, resolved_settings.opencode_base_url)
    resolved_hooks = hooks or DefaultOrchestratorHooks()
    resolved_client = opencode_client or OpenCodeHTTP(resolved_settings)
    service = OrchestratorService(
        settings=resolved_settings,
        db=db,
        opencode=resolved_client,
        hooks=resolved_hooks,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await service.start()
        try:
            yield
        finally:
            await service.stop()

    app = FastAPI(
        title=resolved_settings.app_name,
        version="1.1.0",
        description="Local-only OpenCode wrapper for OpenWebUI.",
        lifespan=lifespan,
    )

    app.state.settings = resolved_settings
    app.state.db = db
    app.state.opencode = resolved_client
    app.state.hooks = resolved_hooks
    app.state.service = service

    @app.get("/health", operation_id="oc_health", tags=["meta"])
    async def health(request: Request):
        return await request.app.state.service.health_payload()

    @app.get("/oc/session/list", operation_id="oc_session_list", tags=["session"])
    async def oc_session_list(
        request: Request,
        workspace_root: Optional[str] = Query(default=None),
        include_archived: bool = Query(default=False),
        include_remote_unbound: bool = Query(default=False),
    ):
        return await request.app.state.service.list_sessions(
            request,
            workspace_root=workspace_root,
            include_archived=include_archived,
            include_remote_unbound=include_remote_unbound,
        )

    @app.post("/oc/session/ensure", operation_id="oc_session_ensure", tags=["session"])
    async def oc_session_ensure(request: Request, body: SessionEnsureRequest):
        return await request.app.state.service.session_ensure(
            request,
            workspace_root=body.workspace_root,
            slot=body.slot,
            title=body.title,
            preferred_agent=body.preferred_agent,
            auto_create=body.auto_create,
        )

    @app.post("/oc/session/attach", operation_id="oc_session_attach", tags=["session"])
    async def oc_session_attach(request: Request, body: SessionAttachRequest):
        return await request.app.state.service.session_attach(
            request,
            opencode_session_id=body.opencode_session_id,
            workspace_root=body.workspace_root,
            slot=body.slot,
            title=body.title,
            preferred_agent=body.preferred_agent,
            archive_existing=body.archive_existing,
        )

    @app.post("/oc/session/archive", operation_id="oc_session_archive", tags=["session"])
    async def oc_session_archive(request: Request, body: SessionArchiveRequest):
        return await request.app.state.service.session_archive(
            request,
            session_ref=body.session_ref,
        )

    @app.post("/oc/task/sync", operation_id="oc_task_sync", tags=["task"])
    async def oc_task_sync(request: Request, body: TaskRequest):
        return await request.app.state.service.task_sync(
            request,
            session_ref=body.session_ref,
            workspace_root=body.workspace_root,
            slot=body.slot,
            title=body.title,
            prompt=body.prompt,
            briefing=body.briefing,
            agent=body.agent,
            model_payload=body.model.to_opencode() if body.model else None,
            create_if_missing=body.create_if_missing,
            include_diff=body.include_diff,
            include_todos=body.include_todos,
            max_messages_for_status=body.max_messages_for_status,
        )

    @app.post("/oc/task/async", operation_id="oc_task_async", tags=["task"])
    async def oc_task_async(request: Request, body: TaskRequest):
        return await request.app.state.service.task_async(
            request,
            session_ref=body.session_ref,
            workspace_root=body.workspace_root,
            slot=body.slot,
            title=body.title,
            prompt=body.prompt,
            briefing=body.briefing,
            agent=body.agent,
            model_payload=body.model.to_opencode() if body.model else None,
            create_if_missing=body.create_if_missing,
            include_diff=body.include_diff,
            include_todos=body.include_todos,
            max_messages_for_status=body.max_messages_for_status,
        )

    @app.post("/oc/status", operation_id="oc_status", tags=["task"])
    async def oc_status(request: Request, body: StatusRequest):
        return await request.app.state.service.status(
            request,
            session_ref=body.session_ref,
            workspace_root=body.workspace_root,
            slot=body.slot,
            include_diff=body.include_diff,
            include_todos=body.include_todos,
            max_messages=body.max_messages,
        )

    @app.get("/oc/permission/list", operation_id="oc_permission_list", tags=["permission"])
    async def oc_permission_list(
        request: Request,
        session_ref: Optional[str] = Query(default=None),
        workspace_root: Optional[str] = Query(default=None),
        slot: str = Query(default="default"),
    ):
        return await request.app.state.service.permission_list(
            request,
            session_ref=session_ref,
            workspace_root=workspace_root,
            slot=slot,
        )

    @app.post("/oc/permission/reply", operation_id="oc_permission_reply", tags=["permission"])
    async def oc_permission_reply(request: Request, body: PermissionReplyRequest):
        return await request.app.state.service.permission_reply(
            request,
            session_ref=body.session_ref,
            workspace_root=body.workspace_root,
            slot=body.slot,
            permission_id=body.permission_id,
            response=body.response,
        )

    @app.post("/oc/abort", operation_id="oc_abort", tags=["task"])
    async def oc_abort(request: Request, body: AbortRequest):
        return await request.app.state.service.abort(
            request,
            session_ref=body.session_ref,
            workspace_root=body.workspace_root,
            slot=body.slot,
        )

    @app.post("/oc/fork", operation_id="oc_fork", tags=["session"])
    async def oc_fork(request: Request, body: SessionForkRequest):
        return await request.app.state.service.fork(
            request,
            session_ref=body.session_ref,
            workspace_root=body.workspace_root,
            slot=body.slot,
            new_slot=body.new_slot,
            title=body.title,
            message_id=body.message_id,
        )

    @app.post("/oc/diff", operation_id="oc_diff", tags=["task"])
    async def oc_diff(request: Request, body: DiffRequest):
        return await request.app.state.service.diff(
            request,
            session_ref=body.session_ref,
            workspace_root=body.workspace_root,
            slot=body.slot,
            message_id=body.message_id,
        )

    return app
