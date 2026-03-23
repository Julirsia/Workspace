## Project Map

This repository is a local OpenWebUI-to-OpenCode orchestrator.

Read in this order when starting work:

1. `README.md`
2. `app.py`
3. The relevant file under `orchestrator/`
4. The matching tests under `tests/`

Quick map:

- `app.py`: thin entrypoint only
- `orchestrator/api.py`: FastAPI routes and app wiring
- `orchestrator/service.py`: shared orchestration core
- `orchestrator/client.py`: OpenCode HTTP adapter
- `orchestrator/store.py`: SQLite persistence
- `orchestrator/hooks.py`: internal customization points
- `openwebui_action_opencode_approval.py`: separate OpenWebUI Action Function integration

## Edit Boundaries

- Prefer `orchestrator/hooks.py` and `orchestrator/config.py` for customization.
- Keep `orchestrator/api.py` thin. Do not move business logic into routes.
- Treat `orchestrator/service.py` as shared core. Edit it only when the behavior cannot be expressed through hooks, config, client, or store changes.
- Avoid changing HTTP request or response shapes unless the user explicitly asks for API changes.
- Do not edit `openwebui_action_opencode_approval.py` unless the task is specifically about the Action Function.
- Prefer narrow changes. Avoid broad renames, file moves, formatting-only diffs, and mixed cleanup.

## Task To File

Use this routing table before scanning the whole repo:

- Change prompt body, result normalization, session title, or session_ref rules:
  - `orchestrator/hooks.py`
- Change environment variables, defaults, timeout config, or validation:
  - `orchestrator/config.py`
- Change route definitions, request models, or app startup wiring:
  - `orchestrator/api.py`
- Change session lifecycle, lazy-create behavior, status assembly, permission handling, abort/fork/diff behavior:
  - `orchestrator/service.py`
- Change OpenCode API path usage, auth handling, or transport behavior:
  - `orchestrator/client.py`
- Change SQLite schema or persistence logic:
  - `orchestrator/store.py`
- Change Action Function button behavior:
  - `openwebui_action_opencode_approval.py`
- Check expected behavior before or after edits:
  - `tests/test_sessions.py`
  - `tests/test_status.py`
  - `tests/test_hooks.py`
  - `tests/test_action.py`

## Upstream-Sync Rule

- This repo should stay easy to sync into internal forks later.
- Prefer adding or adjusting localized extension points over rewriting shared core files.
- When a shared-core edit is unavoidable, keep it small and explain why it was necessary.
