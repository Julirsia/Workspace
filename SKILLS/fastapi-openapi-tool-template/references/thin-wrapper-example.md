# Thin Wrapper Example

Use this pattern when a model should call one clean tool endpoint while the server hides internal complexity.

## Recommended shape

- One POST endpoint
- One request model
- One response model
- One small adapter function for the internal system

## Why this shape is good for smaller models

- Fewer fields to reason about
- One obvious entrypoint
- One place to replace mock logic with real logic
- OpenAPI docs stay short and clean

## Suggested request shape

```python
class SearchRequest(BaseModel):
    query: str
    limit: int = 10
```

## Suggested response shape

```python
class SearchItem(BaseModel):
    id: str
    title: str

class SearchResponse(BaseModel):
    items: list[SearchItem]
    count: int
```

## Suggested implementation shape

```python
def call_internal_service(query: str, limit: int) -> list[dict]:
    # replace this mock later
    return [{"id": "demo-1", "title": f"Result for {query}"}][:limit]
```

Then keep the endpoint thin:

```python
@app.post("/search-specs", response_model=SearchResponse)
def search_specs(body: SearchRequest) -> SearchResponse:
    rows = call_internal_service(body.query, body.limit)
    items = [SearchItem(**row) for row in rows]
    return SearchResponse(items=items, count=len(items))
```

## Safe migration path

1. Keep the request and response schema stable.
2. Replace only the adapter function.
3. Re-run curl test.
4. Re-open `/docs`.
