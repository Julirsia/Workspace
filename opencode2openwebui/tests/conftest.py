import asyncio
import importlib
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tests.fake_opencode import FakeOpenCode


@pytest.fixture
def app_env(monkeypatch, tmp_path):
    monkeypatch.setenv("ORCH_DB_PATH", str(tmp_path / "orchestrator.db"))
    monkeypatch.setenv("OPENCODE_BASE_URL", "http://127.0.0.1:4096")
    monkeypatch.setenv("ENABLE_OPENCODE_EVENT_LISTENER", "false")
    monkeypatch.setenv("ALLOW_MISSING_OWUI_HEADERS", "false")

    sys.modules.pop("app", None)
    module = importlib.import_module("app")
    old_client = module.opencode
    asyncio.run(old_client.close())

    fake = FakeOpenCode()
    module.opencode = fake
    module._FIXED_WORKSPACE_ROOT_CACHE = None

    with TestClient(module.app) as client:
        yield module, fake, client


@pytest.fixture
def owui_headers():
    return {
        "X-OpenWebUI-User-Id": "user-1",
        "X-OpenWebUI-Chat-Id": "chat-1",
    }
