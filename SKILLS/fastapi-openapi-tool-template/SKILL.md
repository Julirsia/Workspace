---
name: fastapi-openapi-tool-template
description: Scaffold a minimal FastAPI service that exposes a clean OpenAPI schema for tool use. Use when asked to bootstrap, generate, or create a small FastAPI/OpenAPI tool server, especially for Open WebUI tool-server integration, function-calling demos, or thin wrapper APIs. Prefer this skill when a smaller model needs a narrow, explicit, low-ambiguity workflow with copyable commands and a minimal number of files.
---

# FastAPI OpenAPI Tool Template

Create a minimal FastAPI project that Open WebUI or any OpenAPI-capable client can ingest as tools.

Default to a **thin-wrapper endpoint** shape instead of a toy echo endpoint.

## Default rule

Prefer the bundled scaffold script over hand-writing boilerplate.

If the request is simple, follow this exact sequence and do not improvise:
1. Run the scaffold script.
2. Edit only the marked `CHANGE` sections in the generated `main.py`.
3. Start the server.
4. Run the generated curl command.
5. Open `/docs` and verify the endpoint appears.

## Command to run first

```bash
python3 scripts/scaffold_fastapi_openapi_template.py \
  --output <target-dir> \
  --app-title "<app title>" \
  --tool-name "<tool_name>" \
  --tool-summary "<short summary>"
```

Useful options:
- `--tool-path /custom-path` to override the default endpoint path
- `--app-description "..."` to adjust the OpenAPI description
- `--force` to overwrite files in an existing output directory

## What the script generates

Generate only the files needed to start and test:
- `main.py` — FastAPI app with one thin-wrapper style POST endpoint and explicit `CHANGE` markers
- `requirements.txt` — FastAPI, Uvicorn, Pydantic
- `.gitignore`
- `sample_request.json` — example request payload for quick manual testing
- `curl_example.sh` — copyable test command

## Small-model workflow

Use this workflow when you want the safest path with the fewest decisions.

1. Run the scaffold script.
2. Read `references/small-model-playbook.md`.
3. Read the generated `main.py`.
4. Replace the example request and response schema with the real schema.
5. Replace the example business logic inside the endpoint.
6. If needed, replace the mock internal adapter function with a real API, DB, or service call.
7. Install dependencies and start Uvicorn.
8. Run the generated curl example.
9. Open `/docs`.
10. Stop only after the endpoint, schema, and sample request all match the intended tool contract.

## Design rules

- Expose only LLM-usable endpoints on the tool server when possible.
- Keep request and response schemas small and explicit.
- Set a stable `operation_id` so the imported tool name is predictable.
- Write short `summary` text that explains the tool action clearly.
- Avoid mixing health, admin, metrics, and internal endpoints into the same OpenAPI surface unless you want them exposed as tools.
- Prefer one thin wrapper endpoint per task over dumping an internal CRUD API directly into the tool surface.
- If unsure, keep one endpoint only.
- Start with a mock implementation first, then replace only the adapter function.

## When to read references

- Read `references/small-model-playbook.md` when you want the narrowest possible execution path.
- Read `references/openwebui-notes.md` when the target client is Open WebUI and you need compatibility reminders.
- Read `references/thin-wrapper-example.md` when you want a concrete wrapper pattern to copy.

## Run command

```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Then open `/docs` or register the base URL in Open WebUI.
