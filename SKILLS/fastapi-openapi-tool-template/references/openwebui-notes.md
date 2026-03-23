# Open WebUI Notes

## Compatibility reminders

- Open WebUI ingests OpenAPI specs and exposes endpoints as tools.
- Stable `operation_id` names help keep imported tool names predictable.
- Short `summary` text improves tool selection quality.
- Smaller request and response schemas are usually easier for models to use reliably.

## Recommended shape

- One endpoint per task
- One request model
- One response model
- One clear summary line

## Avoid

- Large generic CRUD surfaces
- Ambiguous parameter names
- Overloaded endpoints with many optional behaviors
- Mixing admin/internal endpoints into the same tool server unless intentional
