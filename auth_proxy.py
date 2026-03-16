"""Auth proxy in front of MCP. Accepts Bearer token or Basic auth (password = token)."""
import base64
import os

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import Response, StreamingResponse

MCP_ACCESS_TOKEN = os.environ.get("MCP_ACCESS_TOKEN", "").strip()
UPSTREAM = os.environ.get("MCP_UPSTREAM", "http://127.0.0.1:3001").rstrip("/")

if not MCP_ACCESS_TOKEN:
    raise ValueError("MCP_ACCESS_TOKEN must be set")

app = FastAPI()


def _check_auth(request: Request) -> bool:
    auth = request.headers.get("Authorization")
    if not auth:
        return False
    if auth.startswith("Bearer "):
        return auth[7:].strip() == MCP_ACCESS_TOKEN
    if auth.startswith("Basic "):
        try:
            decoded = base64.b64decode(auth[6:].strip()).decode("utf-8", errors="strict")
            _, password = decoded.split(":", 1)
            return password == MCP_ACCESS_TOKEN
        except Exception:
            return False
    return False


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def proxy(path: str, request: Request):
    if not _check_auth(request):
        return Response(content='{"error":"Unauthorized"}', status_code=401)

    url = f"{UPSTREAM}/{path}" if path else UPSTREAM
    headers = dict(request.headers)
    headers.pop("host", None)
    body = await request.body()

    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            r = await client.request(
                request.method,
                url,
                content=body,
                headers=headers,
            )
        except httpx.ConnectError as e:
            return Response(content=str(e), status_code=502)

    if r.headers.get("content-type", "").startswith("text/event-stream"):
        return StreamingResponse(
            r.aiter_bytes(),
            status_code=r.status_code,
            headers=dict(r.headers),
            media_type=r.headers.get("content-type"),
        )
    return Response(
        content=r.content,
        status_code=r.status_code,
        headers={k: v for k, v in r.headers.items() if k.lower() not in ("transfer-encoding",)},
    )
