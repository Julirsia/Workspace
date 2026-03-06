def test_status_returns_not_started_for_new_chat(app_env, owui_headers):
    _, _, client = app_env
    response = client.post("/oc/status", headers=owui_headers, json={"slot": "default"})
    assert response.status_code == 200
    data = response.json()
    assert data["started"] is False
    assert data["binding"] is None
    assert data["needs_approval"] is False


def test_permission_list_returns_not_started_for_new_chat(app_env, owui_headers):
    _, _, client = app_env
    response = client.get("/oc/permission/list?slot=default", headers=owui_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["started"] is False
    assert data["count"] == 0
    assert data["pending_permissions"] == []


def test_task_sync_parses_json_result(app_env, owui_headers):
    _, fake, client = app_env
    fake.next_sync_text = (
        '{"summary":"fixed","deliverables":["src/app.py"],'
        '"tests_run":[{"command":"pytest","status":"passed","details":"1 passed"}],'
        '"risks":[],"next_action":"commit"}'
    )
    response = client.post(
        "/oc/task/sync",
        headers=owui_headers,
        json={"slot": "default", "prompt": "OpenCode로 짧게 처리해줘"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["created_session"] is True
    assert data["result"]["summary"] == "fixed"
    assert data["result"]["deliverables"] == ["src/app.py"]
