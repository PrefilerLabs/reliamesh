"""Authenticated, content-free reliability ingestion and inspection API."""

import hmac
import logging
import re
from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from reliamesh import __version__
from reliamesh.config import Settings, make_store
from reliamesh.detection import DetectionError, process_events, summarize
from reliamesh.protocol import EventBatch
from reliamesh.security import Limiter, digest_key, new_key
from reliamesh.storage import StorageLimit, TenantExists, TenantMissing

LOG = logging.getLogger("reliamesh")
TenantId = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]{2,63}$")]


class TenantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    tenant_id: TenantId


class KeyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    scopes: list[Literal["ingest", "read", "manage"]] = Field(min_length=1, max_length=3)


class IngestReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal["1.0"]
    accepted: int = Field(ge=0)
    duplicates: int = Field(ge=0)


class ErrorResponse(BaseModel):
    """Public failures contain fixed codes, never request values or exceptions."""

    error: str
    schema_url: str | None = Field(default=None, alias="schema")


TENANT_BEARER = HTTPBearer(
    auto_error=False, scheme_name="TenantBearer", bearerFormat="rm_<43 URL-safe characters>",
    description="Tenant API key. Endpoint descriptions specify the required ingest, read, or manage scope.",
)
ADMIN_BEARER = HTTPBearer(
    auto_error=False, scheme_name="AdministratorBearer", bearerFormat="rm_<43 URL-safe characters>",
    description="Operator administrator key used only to provision tenants. Tenant keys cannot authorize this endpoint.",
)


class SafetyMiddleware:
    """Buffer at most the payload cap before JSON parsing, including chunked bodies."""

    def __init__(self, app, settings):
        self.app, self.settings = app, settings
        self.global_limit = Limiter(settings.global_requests_per_second, 1)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request_id = str(uuid4())
        response_started = False

        async def safe_send(message):
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
                message["headers"] = list(message["headers"]) + [
                    (b"x-request-id", request_id.encode()),
                    (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"no-referrer"),
                    (b"cache-control", b"no-store"),
                    (b"content-security-policy", b"default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'"),
                    (b"strict-transport-security", b"max-age=31536000"),
                ]
            await send(message)

        async def reject(status, code):
            response = JSONResponse({"error": code}, status_code=status)
            if status == 429:
                response.headers["Retry-After"] = "60"
            await response(scope, receive, safe_send)

        if scope["path"] not in {"/health", "/"} and not self.global_limit.allow("all"):
            return await reject(429, "rate_limited")
        headers = dict(scope.get("headers", []))
        if headers.get(b"content-encoding", b"identity") != b"identity":
            return await reject(415, "unsupported_encoding")
        if scope["method"] in {"POST", "PUT", "PATCH"}:
            if headers.get(b"content-type", b"").split(b";")[0] != b"application/json":
                return await reject(415, "application_json_required")
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > self.settings.max_body_bytes:
                return await reject(413, "payload_too_large")
            body.extend(chunk)
            if not message.get("more_body", False):
                break
        # Bound JSON nesting before framework parsing; never echo rejected bytes.
        if body:
            depth = 0
            quoted = escaped = False
            for byte in body:
                if escaped:
                    escaped = False
                elif quoted and byte == 92:
                    escaped = True
                elif byte == 34:
                    quoted = not quoted
                elif not quoted:
                    depth += (byte in (91, 123)) - (byte in (93, 125))
                    if depth > 12:
                        return await reject(422, "json_too_deep")
        consumed = False

        async def bounded_receive():
            nonlocal consumed
            if not consumed:
                consumed = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        try:
            await self.app(scope, bounded_receive, safe_send)
        except Exception:
            # Exception strings/tracebacks may include request data: log fixed metadata only.
            LOG.error("request_failed request_id=%s", request_id)
            if response_started:
                # An ASGI response cannot restart after headers were sent. Abort
                # the stream with a fixed error, never the original exception
                # message or chained customer content in server tracebacks.
                raise RuntimeError("response_failed") from None
            await reject(503, "temporarily_unavailable")


def create_app(settings=None, store=None):
    settings = settings or Settings.from_env()
    store = store or make_store(settings)
    app = FastAPI(
        title="ReliaMesh", version=__version__, docs_url=None, redoc_url=None,
        responses={
            code: {"model": ErrorResponse, "description": description}
            for code, description in {
                401: "Missing, invalid, or revoked credential",
                403: "Credential lacks the required scope",
                409: "Atomic request rejected due to a conflict or bounded resource limit",
                413: "Request body exceeds the configured size limit",
                415: "Unsupported request content type or encoding",
                422: "Invalid request; rejected input values are never echoed",
                429: "Request rate or daily event quota exceeded",
                503: "Service temporarily unavailable",
            }.items()
        },
    )
    app.state.store, app.state.settings = store, settings
    tenant_limiter = Limiter(settings.requests_per_minute, 60)

    def bearer(request):
        header = request.headers.get("authorization", "")
        if not re.fullmatch(r"Bearer rm_[A-Za-z0-9_-]{43}", header):
            raise HTTPException(401, "invalid_credentials")
        return digest_key(header[7:])

    def require(scope):
        def authenticate(
            request: Request,
            _credential: Annotated[HTTPAuthorizationCredentials | None, Depends(TENANT_BEARER)],
        ):
            key_hash = bearer(request)
            principal = store.authenticate(key_hash)
            if not principal or not principal.get("active", True):
                raise HTTPException(401, "invalid_credentials")
            if scope not in principal["scopes"]:
                raise HTTPException(403, "insufficient_scope")
            if not tenant_limiter.allow(principal["tenant_id"]):
                raise HTTPException(429, "rate_limited", headers={"Retry-After": "60"})
            # Bind the eventual storage operation to this credential inside its
            # transaction, including revocation/deletion/recreation races.
            return {**principal, "key_hash": key_hash}
        return authenticate

    def admin(
        request: Request,
        _credential: Annotated[HTTPAuthorizationCredentials | None, Depends(ADMIN_BEARER)],
    ):
        candidate = bearer(request)
        if not settings.admin_hash or not hmac.compare_digest(candidate, settings.admin_hash):
            raise HTTPException(401, "invalid_credentials")

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        # Even field names can be caller content: return a fixed error only.
        return JSONResponse({"error": "invalid_request", "schema": "/openapi.json"}, status_code=422)

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code, headers=exc.headers)

    @app.exception_handler(DetectionError)
    async def detection_error(request, exc):
        return JSONResponse({"error": exc.code}, status_code=409)

    @app.exception_handler(StorageLimit)
    async def storage_limit(request, exc):
        return JSONResponse({"error": "storage_limit"}, status_code=409)

    @app.exception_handler(TenantMissing)
    async def tenant_missing(request, exc):
        return JSONResponse({"error": "invalid_credentials"}, status_code=401)

    @app.get("/health")
    def health():
        return {"status": "ok", "version": __version__}

    @app.get("/ready", dependencies=[Depends(require("read"))], description="Requires a tenant API key with read scope.")
    def ready():
        if not store.health():
            raise HTTPException(503, "storage_unavailable")
        return {"status": "ready"}

    @app.post("/v1/admin/tenants", status_code=201, dependencies=[Depends(admin)],
              description="Requires the operator administrator key. Returns the new tenant credential exactly once.")
    def create_tenant(body: TenantRequest):
        key = new_key()
        try:
            store.create_tenant(body.tenant_id, digest_key(key), datetime.now(UTC))
        except TenantExists:
            raise HTTPException(409, "tenant_exists") from None
        return {"tenant_id": body.tenant_id, "key": key, "scopes": ["ingest", "read", "manage"]}

    @app.post("/v1/events", response_model=IngestReceipt,
              description="Requires a tenant API key with ingest scope. Accepts 1–100 events atomically; identical retained retries are duplicates.")
    def ingest(body: EventBatch, principal: Annotated[dict, Depends(require("ingest"))]):
        now = datetime.now(UTC)

        def update(state):
            usage = state.get("usage", {})
            today = now.date().isoformat()
            used = usage.get("events", 0) if usage.get("day") == today else 0
            new_state, result = process_events(state, body.events, now)
            if used + result["accepted"] > settings.daily_events:
                raise HTTPException(429, "daily_event_quota", headers={"Retry-After": "3600"})
            new_state["usage"] = {"day": today, "events": used + result["accepted"]}
            return new_state, result

        result = store.transact(principal["tenant_id"], update, expected_key_hash=principal["key_hash"])
        return {"schema_version": "1.0", **result}

    @app.get("/v1/summary", description="Requires a tenant API key with read scope. Returns bounded tenant-local window statistics, versions, and incident history.")
    def summary(principal: Annotated[dict, Depends(require("read"))]):
        state = store.read_state(principal["tenant_id"], expected_key_hash=principal["key_hash"])
        if state is None:
            raise HTTPException(401, "invalid_credentials")
        return summarize(state, datetime.now(UTC))

    @app.get("/v1/incidents", description="Requires a tenant API key with read scope. Returns this tenant's retained incidents only.")
    def incidents(principal: Annotated[dict, Depends(require("read"))]):
        return {"incidents": summary(principal)["incidents"]}

    @app.get("/v1/keys", description="Requires a tenant API key with manage scope. Returns key identifiers and scopes, never raw credentials or full digests.")
    def keys(principal: Annotated[dict, Depends(require("manage"))]):
        return {"keys": store.list_keys(principal["tenant_id"], expected_key_hash=principal["key_hash"])}

    @app.post("/v1/keys", status_code=201, description="Requires manage scope. Requested scopes must be a subset of the caller's scopes. The new credential is returned exactly once.")
    def add_key(body: KeyRequest, principal: Annotated[dict, Depends(require("manage"))]):
        if not set(body.scopes).issubset(principal["scopes"]):
            raise HTTPException(403, "scope_escalation")
        key = new_key()
        store.add_key(principal["tenant_id"], digest_key(key), sorted(set(body.scopes)), datetime.now(UTC),
                      expected_key_hash=principal["key_hash"])
        return {"key": key, "key_id": digest_key(key)[:12], "scopes": body.scopes}

    @app.delete("/v1/keys/{key_id}", description="Requires a tenant API key with manage scope. Revokes one key belonging to this tenant.")
    def revoke_key(key_id: str, principal: Annotated[dict, Depends(require("manage"))]):
        if not re.fullmatch(r"[0-9a-f]{12}", key_id):
            raise HTTPException(422, "invalid_key_id")
        removed = store.revoke_key(principal["tenant_id"], key_id, expected_key_hash=principal["key_hash"])
        if not removed:
            raise HTTPException(404, "key_not_found")
        return {"revoked": True}

    @app.delete("/v1/tenant", description="Requires manage scope and X-ReliaMesh-Confirm-Delete equal to the authenticated tenant ID. Permanently deletes that tenant's current state and keys.")
    def delete_tenant(request: Request, principal: Annotated[dict, Depends(require("manage"))]):
        if request.headers.get("x-reliamesh-confirm-delete") != principal["tenant_id"]:
            raise HTTPException(400, "tenant_confirmation_required")
        store.delete_tenant(principal["tenant_id"], expected_key_hash=principal["key_hash"])
        return {"deleted": True}

    @app.get("/v1/network", dependencies=[Depends(require("read"))], description="Requires a tenant API key with read scope. Network intelligence is disabled and returns no incidents.")
    def network():
        return {"status": "disabled", "reason": "No verified independent contributor cohort is configured.", "incidents": []}

    @app.get("/", response_class=HTMLResponse)
    def home():
        return LANDING

    app.add_middleware(SafetyMiddleware, settings=settings)
    return app


LANDING = """<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ReliaMesh — Reliability infrastructure for AI agents</title>
<style>body{margin:0;background:#101b24;color:#eaf0f3;font:18px/1.7 system-ui,sans-serif}
main{max-width:880px;margin:auto;padding:9vh 7vw}small{color:#7edcbd;letter-spacing:.13em}
h1{font-size:clamp(36px,6vw,66px);line-height:1.12;letter-spacing:-.04em}p{color:#b9cad5;max-width:720px}
a{color:#7edcbd}code{background:#1d303b;padding:4px 8px}footer{border-top:1px solid #304550;margin-top:70px;padding-top:20px;font-size:14px}
</style><main><small>RELIAMESH / OPEN-SOURCE INFRASTRUCTURE</small>
<h1>When an agent runs,<br>did it actually work?</h1>
<p>Measure agent failures, detect version-associated regressions, and track recovery
using structured reliability signals. API uptime alone does not tell that story.</p>
<h2>Reliability without customer content.</h2>
<p>Send outcomes, durations, counters, and opaque version identifiers. The event contract
rejects prompts, responses, tool arguments, arbitrary attributes, and exception messages.</p>
<p>Authenticated ingestion · Python SDK · Deterministic detection · SQLite self-hosting</p>
<p><a href="/openapi.json">Versioned API contract</a> · <a href="/health">Service health</a></p>
<h2>Early release. Evidence first.</h2><p>Managed access is provisioned by the operator.
Cross-organization network intelligence is disabled until independent, verified evidence exists.
No customer counts, performance claims, or sample incidents are presented as real adoption.</p>
<footer>© 2026 Prefiler Labs Private Limited. ReliaMesh core is licensed under Apache-2.0.</footer>
</main></html>"""



