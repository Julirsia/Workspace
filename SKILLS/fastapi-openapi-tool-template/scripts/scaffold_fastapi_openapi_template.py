#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import json
import re

MAIN_TEMPLATE = '''from fastapi import FastAPI
from pydantic import BaseModel, Field

# CHANGE 1: Update title and description only if needed.
app = FastAPI(
    title={app_title!r},
    version="0.1.0",
    description={app_description!r},
)


# CHANGE 2: Replace these example fields with the real request schema.
class {request_model}(BaseModel):
    query: str = Field(..., description="User query")
    limit: int = Field(10, ge=1, le=50, description="Maximum number of results")


class {item_model}(BaseModel):
    id: str = Field(..., description="Result identifier")
    title: str = Field(..., description="Result title")


# CHANGE 3: Replace these example fields with the real response schema.
class {response_model}(BaseModel):
    items: list[{item_model}] = Field(..., description="Returned result items")
    count: int = Field(..., description="Number of returned items")


# CHANGE 4: Replace this adapter with a real internal API, DB, or service call.
def _call_internal_service(query: str, limit: int) -> list[dict]:
    demo_rows = [
        {{"id": "demo-1", "title": f"Result for {{query}}"}},
        {{"id": "demo-2", "title": f"Another result for {{query}}"}},
    ]
    return demo_rows[:limit]


@app.post(
    {tool_path!r},
    operation_id={operation_id!r},
    summary={tool_summary!r},
    response_model={response_model},
    tags=["tools"],
)
def {function_name}(body: {request_model}) -> {response_model}:
    rows = _call_internal_service(query=body.query, limit=body.limit)
    items = [{item_model}(**row) for row in rows]
    return {response_model}(items=items, count=len(items))
'''

REQUIREMENTS_TXT = '''fastapi>=0.115,<1.0
uvicorn[standard]>=0.30,<1.0
pydantic>=2.0,<3.0
'''

GITIGNORE = '''__pycache__/
*.pyc
.venv/
.env
'''


def to_snake_case(value: str) -> str:
    value = value.strip()
    value = re.sub(r"[^a-zA-Z0-9]+", "_", value)
    value = re.sub(r"_+", "_", value)
    return value.strip("_").lower() or "search_specs"


def to_kebab_case(value: str) -> str:
    return to_snake_case(value).replace("_", "-")


def to_pascal_case(value: str) -> str:
    return "".join(part.capitalize() for part in to_snake_case(value).split("_")) or "SearchSpecs"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scaffold a minimal FastAPI OpenAPI tool server template."
    )
    parser.add_argument("--output", required=True, help="Output directory for the template")
    parser.add_argument(
        "--app-title",
        default="Example Tool Server",
        help="FastAPI app title shown in OpenAPI docs",
    )
    parser.add_argument(
        "--app-description",
        default="Minimal FastAPI thin-wrapper tool server exposing a single OpenAPI endpoint.",
        help="FastAPI app description shown in OpenAPI docs",
    )
    parser.add_argument(
        "--tool-name",
        default="search_specs",
        help="Logical tool name used for operation_id and function naming",
    )
    parser.add_argument(
        "--tool-summary",
        default="Search internal specs",
        help="Short tool summary shown in OpenAPI docs",
    )
    parser.add_argument(
        "--tool-path",
        default=None,
        help="HTTP path for the tool endpoint (defaults to /<tool-name> in kebab-case)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing files in the output directory",
    )
    return parser.parse_args()


def ensure_output_dir(path: Path, force: bool) -> None:
    if path.exists() and any(path.iterdir()) and not force:
        raise SystemExit(
            f"Output directory is not empty: {path}\nUse --force to overwrite files."
        )
    path.mkdir(parents=True, exist_ok=True)


def write_text(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    print(f"[OK] Wrote {path}")


def main() -> None:
    args = parse_args()
    output_dir = Path(args.output).expanduser().resolve()
    ensure_output_dir(output_dir, args.force)

    function_name = to_snake_case(args.tool_name)
    operation_id = function_name
    tool_path = args.tool_path or f"/{to_kebab_case(args.tool_name)}"
    base_name = to_pascal_case(args.tool_name)
    request_model = f"{base_name}Request"
    item_model = f"{base_name}Item"
    response_model = f"{base_name}Response"

    main_py = MAIN_TEMPLATE.format(
        app_title=args.app_title,
        app_description=args.app_description,
        request_model=request_model,
        item_model=item_model,
        response_model=response_model,
        tool_path=tool_path,
        operation_id=operation_id,
        tool_summary=args.tool_summary,
        function_name=function_name,
    )

    sample_request = {"query": "vacuum pump alarm", "limit": 2}
    curl_example = (
        f"curl -X POST 'http://localhost:8000{tool_path}' \\\n"
        "  -H 'Content-Type: application/json' \\\n"
        f"  -d '{json.dumps(sample_request, ensure_ascii=False)}'\n"
    )

    write_text(output_dir / "main.py", main_py)
    write_text(output_dir / "requirements.txt", REQUIREMENTS_TXT)
    write_text(output_dir / ".gitignore", GITIGNORE)
    write_text(
        output_dir / "sample_request.json",
        json.dumps(sample_request, ensure_ascii=False, indent=2) + "\n",
    )
    write_text(output_dir / "curl_example.sh", curl_example)

    print("\nNext steps:")
    print(f"1. cd {output_dir}")
    print("2. python3 -m venv .venv && source .venv/bin/activate")
    print("3. pip install -r requirements.txt")
    print("4. uvicorn main:app --host 0.0.0.0 --port 8000 --reload")
    print("5. Run: sh curl_example.sh")
    print("6. Open http://localhost:8000/docs or connect the server URL in Open WebUI")


if __name__ == "__main__":
    main()
