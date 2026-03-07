import os
from typing import Any, Dict, List, Optional, Protocol

import httpx
from fastapi import HTTPException

from .config import Settings


def _normalize_path(path: str) -> str:
    return os.path.abspath(os.path.expanduser(path))


def _opencode_auth(settings: Settings) -> Optional[httpx.BasicAuth]:
    if settings.opencode_password:
        return httpx.BasicAuth(settings.opencode_username, settings.opencode_password)
    return None


class OpenCodeClient(Protocol):
    async def close(self) -> None: ...

    async def health(self) -> Dict[str, Any]: ...

    async def current_project_root(self) -> Optional[str]: ...

    async def list_sessions(self) -> List[Dict[str, Any]]: ...

    async def get_session(self, session_id: str) -> Dict[str, Any]: ...

    async def create_session(self, title: str) -> Dict[str, Any]: ...

    async def get_status_map(self) -> Dict[str, Any]: ...

    async def session_messages(self, session_id: str, limit: int = 6) -> List[Dict[str, Any]]: ...

    async def session_todo(self, session_id: str) -> List[Dict[str, Any]]: ...

    async def session_diff(self, session_id: str, message_id: Optional[str] = None) -> List[Dict[str, Any]]: ...

    async def prompt_sync(self, session_id: str, body: Dict[str, Any], timeout_seconds: float) -> Dict[str, Any]: ...

    async def prompt_async(self, session_id: str, body: Dict[str, Any]) -> None: ...

    async def reply_permission(self, session_id: str, permission_id: str, response: str) -> bool: ...

    async def abort(self, session_id: str) -> bool: ...

    async def fork(self, session_id: str, message_id: Optional[str] = None) -> Dict[str, Any]: ...


class OpenCodeHTTP:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = httpx.AsyncClient(
            base_url=settings.opencode_base_url,
            auth=_opencode_auth(settings),
            timeout=httpx.Timeout(
                connect=settings.connect_timeout_s,
                read=settings.read_timeout_s,
                write=settings.write_timeout_s,
                pool=settings.pool_timeout_s,
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
                return _normalize_path(root)
        except HTTPException:
            pass
        try:
            path_info = await self.json_request("GET", "/path")
        except HTTPException:
            return None
        root = self._extract_path(path_info)
        return _normalize_path(root) if root else None

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
            connect=self._settings.connect_timeout_s,
            read=timeout_seconds,
            write=self._settings.write_timeout_s,
            pool=self._settings.pool_timeout_s,
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
