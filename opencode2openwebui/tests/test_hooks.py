from fastapi.testclient import TestClient

from orchestrator.api import create_app
from orchestrator.config import Settings
from orchestrator.hooks import DefaultOrchestratorHooks, JSON_ONLY_SYSTEM_PROMPT, RESULT_SCHEMA
from tests.fake_opencode import FakeOpenCode


def test_default_hooks_build_prompt_body_matches_current_behavior():
    hooks = DefaultOrchestratorHooks()
    body = hooks.build_prompt_body(
        prompt="OpenCode로 처리해줘",
        agent="owui-worker",
        model_payload={"providerID": "openai", "modelID": "gpt-5"},
        use_structured_output=True,
    )
    assert body == {
        "parts": [{"type": "text", "text": "OpenCode로 처리해줘"}],
        "system": JSON_ONLY_SYSTEM_PROMPT,
        "agent": "owui-worker",
        "model": {"providerID": "openai", "modelID": "gpt-5"},
        "format": {
            "type": "json_schema",
            "schema": RESULT_SCHEMA,
            "retryCount": 1,
        },
    }


def test_default_hooks_build_briefing_body_matches_current_behavior():
    hooks = DefaultOrchestratorHooks()
    body = hooks.build_briefing_body(
        briefing="  context to pass along  ",
        agent="owui-worker",
        model_payload={"providerID": "openai", "modelID": "gpt-5"},
    )
    assert body == {
        "noReply": True,
        "parts": [{"type": "text", "text": "[OpenWebUI handoff]\ncontext to pass along"}],
        "agent": "owui-worker",
        "model": {"providerID": "openai", "modelID": "gpt-5"},
    }


def test_default_hooks_normalize_result_fallback_matches_current_behavior():
    hooks = DefaultOrchestratorHooks()
    result = hooks.normalize_result(
        raw_text="plain text result",
        structured_output=None,
    )
    assert result == {
        "summary": "plain text result",
        "deliverables": [],
        "tests_run": [],
        "risks": [],
        "next_action": "",
    }


def test_default_hooks_make_session_title_matches_current_behavior():
    hooks = DefaultOrchestratorHooks()
    assert hooks.make_session_title(workspace_root="/tmp/repo", slot="review", explicit_title=None) == "owui:repo:review"
    assert hooks.make_session_title(workspace_root="/tmp/repo", slot="review", explicit_title="  custom title  ") == "custom title"


def test_default_hooks_make_session_ref_matches_current_behavior():
    hooks = DefaultOrchestratorHooks()
    session_ref = hooks.make_session_ref(
        opencode_base_url="http://127.0.0.1:4096",
        user_id="user-1",
        chat_id="chat-1",
        workspace_root="/repo",
        slot="default",
    )
    assert session_ref == "oc_38539add8bd0c2929ee1"


class CustomHooks(DefaultOrchestratorHooks):
    def build_prompt_body(self, *, prompt, agent, model_payload, use_structured_output):
        body = super().build_prompt_body(
            prompt=prompt,
            agent=agent,
            model_payload=model_payload,
            use_structured_output=use_structured_output,
        )
        body["parts"][0]["text"] = "[internal] {prompt}".format(prompt=prompt)
        return body

    def normalize_result(self, *, raw_text, structured_output):
        result = super().normalize_result(raw_text=raw_text, structured_output=structured_output)
        result["summary"] = "custom-summary"
        return result


def test_custom_hooks_can_change_prompt_and_result_without_route_changes(owui_headers, tmp_path):
    settings = Settings(
        db_path=str(tmp_path / "orchestrator.db"),
        opencode_base_url="http://127.0.0.1:4096",
        event_listener_enabled=False,
        allow_missing_owui_headers=False,
    )
    fake = FakeOpenCode()
    app = create_app(settings=settings, opencode_client=fake, hooks=CustomHooks())

    with TestClient(app) as client:
        response = client.post(
            "/oc/task/sync",
            headers=owui_headers,
            json={"slot": "default", "prompt": "OpenCode로 짧게 처리해줘"},
        )

    assert response.status_code == 200
    assert fake.prompt_sync_bodies[-1]["parts"][0]["text"] == "[internal] OpenCode로 짧게 처리해줘"
    assert response.json()["result"]["summary"] == "custom-summary"
