import asyncio

from openwebui_action_opencode_approval import Action, NOT_STARTED_MESSAGE


async def _noop_emitter(payload):
    return payload


async def _noop_call(payload):
    return None


def test_action_returns_not_started_message_when_no_binding():
    action = Action()

    async def fake_choose_binding(headers, __event_call__=None):
        return None

    action._choose_binding = fake_choose_binding
    result = asyncio.run(
        action.action(
            body={"chat_id": "chat-1"},
            __user__={"id": "user-1"},
            __event_emitter__=_noop_emitter,
            __event_call__=_noop_call,
            __id__="status",
        )
    )
    assert result["content"] == NOT_STARTED_MESSAGE
