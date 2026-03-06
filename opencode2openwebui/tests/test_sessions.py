def test_session_list_is_empty_for_new_chat(app_env, owui_headers):
    _, _, client = app_env
    response = client.get("/oc/session/list", headers=owui_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["active_bindings"] == []


def test_first_task_async_lazy_creates_session(app_env, owui_headers):
    _, fake, client = app_env
    response = client.post(
        "/oc/task/async",
        headers=owui_headers,
        json={"slot": "default", "prompt": "OpenCode로 작업해줘"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["created_session"] is True
    assert fake.create_session_calls == 1


def test_second_task_async_reuses_same_slot_session(app_env, owui_headers):
    _, fake, client = app_env
    first = client.post(
        "/oc/task/async",
        headers=owui_headers,
        json={"slot": "default", "prompt": "OpenCode로 작업해줘"},
    )
    second = client.post(
        "/oc/task/async",
        headers=owui_headers,
        json={"slot": "default", "prompt": "같은 세션으로 계속해줘"},
    )
    assert first.status_code == 200
    assert second.status_code == 200
    assert fake.create_session_calls == 1
    assert first.json()["binding"]["session_ref"] == second.json()["binding"]["session_ref"]
    assert second.json()["created_session"] is False


def test_different_slot_creates_new_session(app_env, owui_headers):
    _, fake, client = app_env
    first = client.post(
        "/oc/task/async",
        headers=owui_headers,
        json={"slot": "default", "prompt": "default에서 작업"},
    )
    second = client.post(
        "/oc/task/async",
        headers=owui_headers,
        json={"slot": "review", "prompt": "review 세션으로 작업"},
    )
    assert first.status_code == 200
    assert second.status_code == 200
    assert fake.create_session_calls == 2
    assert first.json()["binding"]["session_ref"] != second.json()["binding"]["session_ref"]


def test_workspace_root_mismatch_is_rejected(app_env, owui_headers):
    _, _, client = app_env
    response = client.post(
        "/oc/task/async",
        headers=owui_headers,
        json={
            "slot": "default",
            "workspace_root": "/another/repo",
            "prompt": "이건 거부되어야 함",
        },
    )
    assert response.status_code == 400
