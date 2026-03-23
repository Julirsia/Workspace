from typing import Any, Dict, List, Optional

from fastapi import HTTPException


class FakeOpenCode:
    def __init__(self, project_root: str = "/repo") -> None:
        self.project_root = project_root
        self.sessions: Dict[str, Dict[str, Any]] = {}
        self.messages: Dict[str, List[Dict[str, Any]]] = {}
        self.todos: Dict[str, List[Dict[str, Any]]] = {}
        self.diffs: Dict[str, List[Dict[str, Any]]] = {}
        self.status_map: Dict[str, Dict[str, Any]] = {}
        self.prompt_sync_bodies: List[Dict[str, Any]] = []
        self.prompt_async_bodies: List[Dict[str, Any]] = []
        self.create_session_calls = 0
        self.prompt_async_calls = 0
        self.prompt_sync_calls = 0
        self.reply_calls: List[Dict[str, Any]] = []
        self.abort_calls: List[str] = []
        self.next_sync_text = (
            '{"summary":"done","deliverables":["file.py"],'
            '"tests_run":[],"risks":[],"next_action":"review"}'
        )

    async def close(self) -> None:
        return None

    async def health(self) -> Dict[str, Any]:
        return {"ok": True}

    async def current_project_root(self) -> str:
        return self.project_root

    async def list_sessions(self) -> List[Dict[str, Any]]:
        return list(self.sessions.values())

    async def get_session(self, session_id: str) -> Dict[str, Any]:
        session = self.sessions.get(session_id)
        if not session:
            raise HTTPException(status_code=502, detail="session not found")
        return session

    async def create_session(self, title: str) -> Dict[str, Any]:
        self.create_session_calls += 1
        session_id = "ses-{count}".format(count=self.create_session_calls)
        session = {
            "id": session_id,
            "title": title,
            "directory": self.project_root,
            "time": {"created": self.create_session_calls, "updated": self.create_session_calls},
        }
        self.sessions[session_id] = session
        self.messages.setdefault(session_id, [])
        self.todos.setdefault(session_id, [])
        self.diffs.setdefault(session_id, [])
        self.status_map[session_id] = {"type": "idle"}
        return session

    async def get_status_map(self) -> Dict[str, Any]:
        return dict(self.status_map)

    async def session_messages(self, session_id: str, limit: int = 6) -> List[Dict[str, Any]]:
        return self.messages.get(session_id, [])[-limit:]

    async def session_todo(self, session_id: str) -> List[Dict[str, Any]]:
        return self.todos.get(session_id, [])

    async def session_diff(self, session_id: str, message_id: Optional[str] = None) -> List[Dict[str, Any]]:
        return self.diffs.get(session_id, [])

    async def prompt_sync(self, session_id: str, body: Dict[str, Any], timeout_seconds: float) -> Dict[str, Any]:
        self.prompt_sync_calls += 1
        self.prompt_sync_bodies.append(body)
        response = {
            "info": {"role": "assistant"},
            "parts": [{"type": "text", "text": self.next_sync_text}],
        }
        self.messages.setdefault(session_id, []).append(response)
        self.status_map[session_id] = {"type": "idle"}
        return response

    async def prompt_async(self, session_id: str, body: Dict[str, Any]) -> None:
        self.prompt_async_calls += 1
        self.prompt_async_bodies.append(body)
        self.status_map[session_id] = {"type": "busy"}

    async def reply_permission(self, session_id: str, permission_id: str, response: str) -> bool:
        self.reply_calls.append(
            {"session_id": session_id, "permission_id": permission_id, "response": response}
        )
        return True

    async def abort(self, session_id: str) -> bool:
        self.abort_calls.append(session_id)
        self.status_map[session_id] = {"type": "aborted"}
        return True

    async def fork(self, session_id: str, message_id: Optional[str] = None) -> Dict[str, Any]:
        new_session = await self.create_session("fork of {session_id}".format(session_id=session_id))
        return new_session
