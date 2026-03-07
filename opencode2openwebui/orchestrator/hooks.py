import hashlib
import json
import os
import re
from typing import Any, Dict, List, Optional, Protocol


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


class OrchestratorHooks(Protocol):
    def build_prompt_body(
        self,
        *,
        prompt: str,
        agent: Optional[str],
        model_payload: Optional[Dict[str, str]],
        use_structured_output: bool,
    ) -> Dict[str, Any]: ...

    def build_briefing_body(
        self,
        *,
        briefing: str,
        agent: Optional[str],
        model_payload: Optional[Dict[str, str]],
    ) -> Optional[Dict[str, Any]]: ...

    def normalize_result(
        self,
        *,
        raw_text: str,
        structured_output: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]: ...

    def make_session_title(self, *, workspace_root: str, slot: str, explicit_title: Optional[str]) -> str: ...

    def make_session_ref(
        self,
        *,
        opencode_base_url: str,
        user_id: str,
        chat_id: str,
        workspace_root: str,
        slot: str,
    ) -> str: ...


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
        if part.get("type") == "step-finish":
            tool = part.get("tool")
            if tool == "bash":
                cmd = part.get("command") or (part.get("metadata") or {}).get("command")
                if isinstance(cmd, str) and cmd:
                    commands.append(cmd)
    return commands


def parse_jsonish(text: str) -> Optional[Dict[str, Any]]:
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
                candidate = stripped[start : index + 1]
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


class DefaultOrchestratorHooks:
    def build_prompt_body(
        self,
        *,
        prompt: str,
        agent: Optional[str],
        model_payload: Optional[Dict[str, str]],
        use_structured_output: bool,
    ) -> Dict[str, Any]:
        body: Dict[str, Any] = {
            "parts": [{"type": "text", "text": prompt}],
            "system": JSON_ONLY_SYSTEM_PROMPT,
        }
        if agent:
            body["agent"] = agent
        if model_payload:
            body["model"] = model_payload
        if use_structured_output:
            body["format"] = {
                "type": "json_schema",
                "schema": RESULT_SCHEMA,
                "retryCount": 1,
            }
        return body

    def build_briefing_body(
        self,
        *,
        briefing: str,
        agent: Optional[str],
        model_payload: Optional[Dict[str, str]],
    ) -> Optional[Dict[str, Any]]:
        if not briefing.strip():
            return None
        body: Dict[str, Any] = {
            "noReply": True,
            "parts": [{"type": "text", "text": "[OpenWebUI handoff]\n{briefing}".format(briefing=briefing.strip())}],
        }
        if agent:
            body["agent"] = agent
        if model_payload:
            body["model"] = model_payload
        return body

    def normalize_result(
        self,
        *,
        raw_text: str,
        structured_output: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        return normalize_result_json(structured_output or parse_jsonish(raw_text), raw_text)

    def make_session_title(self, *, workspace_root: str, slot: str, explicit_title: Optional[str]) -> str:
        if explicit_title:
            return explicit_title.strip()
        base = os.path.basename(workspace_root.rstrip("/")) or "workspace"
        return "owui:{base}:{slot}".format(base=base, slot=slot)

    def make_session_ref(
        self,
        *,
        opencode_base_url: str,
        user_id: str,
        chat_id: str,
        workspace_root: str,
        slot: str,
    ) -> str:
        raw = "{base}|{user}|{chat}|{root}|{slot}".format(
            base=opencode_base_url,
            user=user_id,
            chat=chat_id,
            root=workspace_root,
            slot=slot,
        )
        return "oc_{digest}".format(digest=hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20])
