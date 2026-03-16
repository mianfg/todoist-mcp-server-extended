"""Auth proxy in front of MCP. Accepts Bearer token or Basic auth (password = token).
Also implements OAuth 2.0 Authorization Code + PKCE for Claude.ai remote MCP flow."""
import base64
import hashlib
import os
import secrets
import time
from urllib.parse import urlencode

import httpx
from fastapi import FastAPI, Request, Form
from fastapi.responses import Response, StreamingResponse, RedirectResponse

MCP_ACCESS_TOKEN = os.environ.get("MCP_ACCESS_TOKEN", "").strip()
UPSTREAM = os.environ.get("MCP_UPSTREAM", "http://127.0.0.1:3001").rstrip("/")

if not MCP_ACCESS_TOKEN:
    raise ValueError("MCP_ACCESS_TOKEN must be set")

app = FastAPI()

# In-memory store for OAuth auth codes: code -> {challenge, method, redirect_uri, state, client_id, expires}
_auth_codes: dict[str, dict] = {}
_CODE_TTL = 300  # 5 minutes


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


def _pkce_verify(verifier: str, challenge: str, method: str) -> bool:
    if method != "S256":
        return False
    digest = hashlib.sha256(verifier.encode()).digest()
    computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return computed == challenge


@app.get("/authorize")
async def oauth_authorize(
    response_type: str = "",
    client_id: str = "",
    redirect_uri: str = "",
    state: str = "",
    code_challenge: str = "",
    code_challenge_method: str = "S256",
    scope: str = "",
):
    """OAuth 2.0 authorize endpoint - immediate redirect with code for Claude.ai flow."""
    if response_type != "code" or not client_id or not redirect_uri or not state or not code_challenge:
        return Response(content='{"error":"invalid_request","error_description":"Missing required params"}', status_code=400)
    code = secrets.token_urlsafe(32)
    _auth_codes[code] = {
        "challenge": code_challenge,
        "method": code_challenge_method,
        "redirect_uri": redirect_uri,
        "state": state,
        "client_id": client_id,
        "expires": time.time() + _CODE_TTL,
    }
    params = {"code": code, "state": state}
    return RedirectResponse(url=f"{redirect_uri}?{urlencode(params)}", status_code=302)


@app.post("/token")
async def oauth_token(
    grant_type: str = Form(""),
    code: str = Form(""),
    redirect_uri: str = Form(""),
    client_id: str = Form(""),
    client_secret: str = Form(""),
    code_verifier: str = Form(""),
):
    """OAuth 2.0 token endpoint - exchange code for access token."""
    if grant_type != "authorization_code" or not code or not client_id or not client_secret or not code_verifier:
        return Response(
            content='{"error":"invalid_request","error_description":"Missing required params"}',
            status_code=400,
        )
    if client_secret != MCP_ACCESS_TOKEN:
        return Response(content='{"error":"invalid_client","error_description":"Invalid client_secret"}', status_code=401)
    info = _auth_codes.pop(code, None)
    if not info:
        return Response(content='{"error":"invalid_grant","error_description":"Invalid or expired code"}', status_code=400)
    if info["expires"] < time.time():
        return Response(content='{"error":"invalid_grant","error_description":"Code expired"}', status_code=400)
    if not _pkce_verify(code_verifier, info["challenge"], info["method"]):
        return Response(content='{"error":"invalid_grant","error_description":"Invalid code_verifier"}', status_code=400)
    return Response(
        content=f'{{"access_token":"{MCP_ACCESS_TOKEN}","token_type":"Bearer","scope":"claudeai"}}',
        media_type="application/json",
    )


def _mcp_path(path: str) -> str:
    """Map semantic paths like /todoist-extended to /mcp."""
    if path in ("todoist-extended", "mcp", ""):
        return "mcp"
    return path


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def proxy(path: str, request: Request):
    if not _check_auth(request):
        return Response(content='{"error":"Unauthorized"}', status_code=401)

    upstream_path = _mcp_path(path)
    url = f"{UPSTREAM}/{upstream_path}" if upstream_path else UPSTREAM
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
