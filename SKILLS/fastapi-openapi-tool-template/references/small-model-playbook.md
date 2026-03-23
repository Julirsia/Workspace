# Small Model Playbook

Use this file when you want the safest, most deterministic path.

## Goal

Produce one working FastAPI OpenAPI tool server with exactly one useful endpoint.

## Do not do these

- Do not add extra frameworks.
- Do not add a database unless the user explicitly asks.
- Do not create multiple endpoints unless the user explicitly asks.
- Do not rename many things at once.
- Do not expose internal CRUD routes directly if one wrapper endpoint is enough.

## Exact steps

1. Run the scaffold script.
2. Open generated `main.py`.
3. Find each `CHANGE` comment.
4. Update only those sections.
5. Keep `FastAPI(...)`, `@app.post(...)`, `operation_id`, and `response_model` explicit.
6. Install dependencies.
7. Run Uvicorn.
8. Execute the generated `curl_example.sh` command.
9. Open `/docs` and confirm the endpoint, request schema, and response schema.

## Minimum success criteria

- Server starts without syntax errors.
- `/docs` opens.
- The endpoint appears in Swagger UI.
- `sample_request.json` matches the request model.
- Curl test returns the response model shape.

## Safe editing rule

If the user request is still vague, change only these four things in `main.py`:
- app title/description
- request model fields
- response model fields
- endpoint business logic

Leave everything else alone.
