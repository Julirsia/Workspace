import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from orchestrator.api import create_app
from orchestrator.config import Settings
from tests.fake_opencode import FakeOpenCode


@pytest.fixture
def app_env(tmp_path):
    settings = Settings(
        db_path=str(tmp_path / "orchestrator.db"),
        opencode_base_url="http://127.0.0.1:4096",
        event_listener_enabled=False,
        allow_missing_owui_headers=False,
    )
    fake = FakeOpenCode()
    app = create_app(settings=settings, opencode_client=fake)

    with TestClient(app) as client:
        yield app, fake, client


@pytest.fixture
def owui_headers():
    return {
        "X-OpenWebUI-User-Id": "user-1",
        "X-OpenWebUI-Chat-Id": "chat-1",
    }
