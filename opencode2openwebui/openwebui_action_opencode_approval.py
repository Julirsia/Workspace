"""
title: OpenCode Approval Helper
author: OpenAI
version: 1.1.0
required_open_webui_version: 0.6.0
"""

import asyncio
import json
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional

from pydantic import BaseModel


NOT_STARTED_MESSAGE = (
    "아직 OpenCode session이 시작되지 않았습니다. "
    "먼저 사용자 요청으로 OpenCode에 작업을 맡겨야 합니다."
)


class Action:
    actions = [
        {"id": "status", "name": "OC Status"},
        {"id": "approve_once", "name": "OC Approve Once"},
        {"id": "approve_always", "name": "OC Approve Always"},
        {"id": "reject", "name": "OC Reject"},
    ]

    class Valves(BaseModel):
        priority: int = 0
        orchestrator_base_url: str = "http://127.0.0.1:8787"
        api_key: str = ""
        default_slot: str = "default"
        fallback_chat_id: str = ""

    def __init__(self):
        self.valves = self.Valves()

    async def _http_json(
        self,
        method: str,
        url: str,
        headers: Dict[str, str],
        payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        def _run():
            req = urllib.request.Request(url, method=method)
            for key, value in headers.items():
                if value:
                    req.add_header(key, value)
            body = None
            if payload is not None:
                body = json.dumps(payload).encode("utf-8")
                req.add_header("Content-Type", "application/json")
            with urllib.request.urlopen(req, data=body, timeout=60) as response:
                return json.loads(response.read().decode("utf-8"))

        return await asyncio.to_thread(_run)

    def _deep_get(self, body: Dict[str, Any], dotted_key: str) -> Optional[str]:
        cursor: Any = body
        for part in dotted_key.split("."):
            if not isinstance(cursor, dict):
                return None
            cursor = cursor.get(part)
        return cursor if isinstance(cursor, str) and cursor else None

    def _guess_chat_id(self, body: Dict[str, Any]) -> str:
        for key in (
            "chat_id",
            "chatId",
            "conversation_id",
            "conversationId",
            "chat.id",
            "metadata.chat_id",
            "metadata.conversation_id",
        ):
            value = self._deep_get(body, key) if "." in key else body.get(key)
            if isinstance(value, str) and value:
                return value
        return self.valves.fallback_chat_id

    async def _choose_binding(self, headers: Dict[str, str], __event_call__=None):
        base = self.valves.orchestrator_base_url.rstrip("/")
        data = await self._http_json("GET", "{base}/oc/session/list".format(base=base), headers)
        bindings = data.get("active_bindings", [])
        if len(bindings) == 1:
            return bindings[0]
        if len(bindings) == 0:
            return None
        if __event_call__:
            options = "\n".join(
                "- {session_ref} :: {title}".format(
                    session_ref=item["session_ref"],
                    title=item["title"],
                )
                for item in bindings
            )
            session_ref = await __event_call__(
                {
                    "type": "input",
                    "data": {
                        "title": "Choose OpenCode session_ref",
                        "message": "여러 OpenCode session이 있습니다.\n{options}".format(options=options),
                        "placeholder": "oc_xxxxxxxxxxxxxxxxxxxx",
                    },
                }
            )
            if session_ref:
                for item in bindings:
                    if item["session_ref"] == session_ref:
                        return item
                return {"session_ref": session_ref}
        return None

    async def action(
        self,
        body: Dict[str, Any],
        __user__=None,
        __event_emitter__=None,
        __event_call__=None,
        __id__=None,
        __request__=None,
        **kwargs,
    ):
        if __event_emitter__:
            await __event_emitter__(
                {"type": "status", "data": {"description": "OpenCode 상태를 확인하는 중입니다..."}}
            )

        user_id = ""
        if isinstance(__user__, dict):
            user_id = str(__user__.get("id") or "")

        chat_id = self._guess_chat_id(body)
        headers = {
            "X-OpenWebUI-User-Id": user_id,
            "X-OpenWebUI-Chat-Id": chat_id,
        }
        if self.valves.api_key:
            headers["x-api-key"] = self.valves.api_key

        binding = await self._choose_binding(headers, __event_call__=__event_call__)
        if not binding:
            return {"content": NOT_STARTED_MESSAGE}

        session_ref = binding["session_ref"]
        base = self.valves.orchestrator_base_url.rstrip("/")

        if __id__ == "status":
            data = await self._http_json(
                "POST",
                "{base}/oc/status".format(base=base),
                headers,
                {"session_ref": session_ref, "slot": self.valves.default_slot},
            )
            if not data.get("started"):
                return {"content": NOT_STARTED_MESSAGE}
            latest = data.get("latest_result") or {}
            perms = data.get("pending_permissions", [])
            return {
                "content": (
                    "session_ref: {session_ref}\n"
                    "status: {status}\n"
                    "needs_approval: {needs_approval}\n"
                    "pending_permissions: {count}\n"
                    "summary: {summary}"
                ).format(
                    session_ref=session_ref,
                    status=json.dumps(data.get("status", {}), ensure_ascii=False),
                    needs_approval=data.get("needs_approval"),
                    count=len(perms),
                    summary=latest.get("summary", ""),
                )
            }

        perm_list = await self._http_json(
            "GET",
            "{base}/oc/permission/list?{query}".format(
                base=base,
                query=urllib.parse.urlencode({"session_ref": session_ref}),
            ),
            headers,
        )
        if not perm_list.get("started"):
            return {"content": NOT_STARTED_MESSAGE}

        pending = perm_list.get("pending_permissions", [])
        if not pending:
            return {"content": "대기 중인 권한 요청이 없습니다. ({session_ref})".format(session_ref=session_ref)}

        permission = pending[0]
        response_map = {
            "approve_once": "once",
            "approve_always": "always",
            "reject": "reject",
        }
        response = response_map.get(__id__)
        if not response:
            return {"content": "알 수 없는 동작입니다."}

        confirmed = True
        if __event_call__:
            confirmed = await __event_call__(
                {
                    "type": "confirmation",
                    "data": {
                        "title": "OpenCode Permission Reply",
                        "message": (
                            "session_ref: {session_ref}\n"
                            "title: {title}\n"
                            "type: {permission_type}\n"
                            "pattern: {pattern}\n\n"
                            "응답: {response}"
                        ).format(
                            session_ref=session_ref,
                            title=permission.get("title"),
                            permission_type=permission.get("permission_type"),
                            pattern=json.dumps(permission.get("pattern"), ensure_ascii=False),
                            response=response,
                        ),
                    },
                }
            )
        if not confirmed:
            return {"content": "취소되었습니다."}

        result = await self._http_json(
            "POST",
            "{base}/oc/permission/reply".format(base=base),
            headers,
            {
                "session_ref": session_ref,
                "permission_id": permission["permission_id"],
                "response": response,
            },
        )
        return {
            "content": (
                "응답 전송 완료: {response}\n"
                "session_ref: {session_ref}\n"
                "permission_id: {permission_id}\n"
                "status: {status}"
            ).format(
                response=response,
                session_ref=session_ref,
                permission_id=permission["permission_id"],
                status=json.dumps(result.get("status_snapshot", {}).get("status", {}), ensure_ascii=False),
            )
        }
