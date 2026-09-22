"""Bounded synchronous delivery with explicit endpoints and explicit flush."""

import ipaddress
import json
import math
import threading
import time
from collections import deque
from contextlib import contextmanager
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .events import event, validate_event


class SDKError(Exception):
    """SDK error with no telemetry values or secrets in its message."""


class DeliveryError(SDKError):
    """A bounded export failed. Response bodies are intentionally omitted."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _endpoint(value: str) -> str:
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
        if (not hostname or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment or "\\" in value
                or any(ord(character) <= 32 for character in value)
                or (port is not None and not 1 <= port <= 65535)):
            raise ValueError
        try:
            local = ipaddress.ip_address(hostname).is_loopback
        except ValueError:
            local = hostname == "localhost"
        if parsed.scheme != "https" and not (parsed.scheme == "http" and local):
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise ValueError("endpoint must be HTTPS or loopback HTTP without credentials/query/fragment") from None
    return value.rstrip("/")


class Client:
    """No worker, persistence, automatic shutdown hook, or implicit cloud endpoint.

    `emit` and `observe` only enqueue. `flush` performs synchronous I/O and drops
    each attempted batch after success or final failure. `failure_mode='raise'`
    additionally raises for invalid events, a full queue, or delivery failure.
    """

    def __init__(self, *, endpoint: str, api_key: str, timeout: float = 2.0,
                 max_retries: int = 2, queue_capacity: int = 1000,
                 failure_mode: str = "drop"):
        self.endpoint = _endpoint(endpoint)
        if (not isinstance(api_key, str) or not 1 <= len(api_key) <= 512
                or any(not 33 <= ord(character) <= 126 for character in api_key)):
            raise ValueError("api_key must be nonempty visible ASCII")
        if type(timeout) not in (int, float) or not 0 < timeout <= 30 or not math.isfinite(timeout):
            raise ValueError("timeout must be greater than zero and at most 30 seconds")
        if type(max_retries) is not int or not 0 <= max_retries <= 3:
            raise ValueError("max_retries must be between zero and three")
        if type(queue_capacity) is not int or not 1 <= queue_capacity <= 10000:
            raise ValueError("queue_capacity must be between one and 10000")
        if failure_mode not in ("drop", "raise"):
            raise ValueError("failure_mode must be drop or raise")
        self._api_key = api_key
        self.timeout = timeout
        self.max_retries = max_retries
        self.queue_capacity = queue_capacity
        self.failure_mode = failure_mode
        self._queue = deque()
        self._lock = threading.Lock()
        self._flush_lock = threading.Lock()
        self._opener = build_opener(_NoRedirect())
        self._counts = dict(enqueued=0, sent=0, dropped=0, failed_batches=0, retries=0)

    @property
    def counters(self) -> dict:
        with self._lock:
            return {**self._counts, "queued": len(self._queue)}

    def _increment(self, name, count=1):
        with self._lock:
            self._counts[name] += count

    def emit(self, value) -> bool:
        """Validate and enqueue a copy. This method performs no network activity."""
        try:
            validated = validate_event(value)
        except (ValueError, TypeError):
            self._increment("dropped")
            if self.failure_mode == "raise":
                raise SDKError("event rejected by local validation") from None
            return False
        with self._lock:
            if len(self._queue) >= self.queue_capacity:
                self._counts["dropped"] += 1
                if self.failure_mode == "raise":
                    raise SDKError("event queue is full")
                return False
            self._queue.append(validated)
            self._counts["enqueued"] += 1
        return True

    def _request(self, path: str, payload: dict | None = None) -> dict | list:
        encoded = None if payload is None else json.dumps(payload, separators=(",", ":"), allow_nan=False).encode()
        headers = {"Authorization": "Bearer " + self._api_key,
                   "Accept": "application/json", "User-Agent": "reliamesh-sdk/0.1.0"}
        if encoded is not None:
            headers["Content-Type"] = "application/json"
        # _endpoint restricts schemes; redirects cannot forward credentials.
        request = Request(self.endpoint + path, data=encoded, headers=headers)  # noqa: S310
        for attempt in range(self.max_retries + 1):
            try:
                with self._opener.open(request, timeout=self.timeout) as response:
                    body = response.read(1_048_577)
                    if len(body) > 1_048_576:
                        raise DeliveryError("server response exceeds SDK size limit")
                    result = json.loads(body)
                    if not isinstance(result, (dict, list)):
                        raise DeliveryError("server returned an invalid response")
                    return result
            except HTTPError as exc:
                status = exc.code
                exc.close()
                if status in (429, 503) and attempt < self.max_retries:
                    self._increment("retries")
                    time.sleep(min(0.2 * 2**attempt, 1.0))
                    continue
                raise DeliveryError(f"request failed with HTTP {status}") from None
            except (URLError, OSError, ValueError, HTTPException):
                raise DeliveryError("request failed or returned invalid JSON") from None
        raise DeliveryError("request failed")

    def flush(self) -> int:
        """Send at most the queue present at entry, in batches of 100 events.

        Only HTTP 429 and 503 retry; retries reuse the identical event IDs/body.
        Delivery is best effort, not a durable delivery guarantee. Unattempted
        batches remain queued if raise mode stops a flush on the first failure.
        """
        sent = 0
        with self._flush_lock:
            with self._lock:
                remaining = len(self._queue)
            while remaining:
                with self._lock:
                    batch = []
                    payload_size = len(b'{"events":[]}')
                    while len(batch) < min(100, remaining):
                        candidate = self._queue[0]
                        event_size = len(json.dumps(candidate, separators=(",", ":"), allow_nan=False).encode())
                        addition = event_size + bool(batch)
                        if batch and payload_size + addition > 131_072:
                            break
                        batch.append(self._queue.popleft())
                        payload_size += addition
                remaining -= len(batch)
                try:
                    result = self._request("/v1/events", {"events": batch})
                    if (not isinstance(result, dict) or result.get("schema_version") != "1.0"
                            or type(result.get("accepted")) is not int
                            or type(result.get("duplicates")) is not int
                            or result["accepted"] < 0 or result["duplicates"] < 0
                            or result["accepted"] + result["duplicates"] != len(batch)):
                        raise DeliveryError("server returned an invalid ingestion acknowledgement")
                except DeliveryError:
                    self._increment("failed_batches")
                    self._increment("dropped", len(batch))
                    if self.failure_mode == "raise":
                        raise
                else:
                    sent += len(batch)
                    self._increment("sent", len(batch))
        return sent

    def summary(self) -> dict | list:
        """Retrieve authenticated tenant summary; failures always raise DeliveryError."""
        return self._request("/v1/summary")

    def incidents(self) -> dict | list:
        """Retrieve authenticated tenant incidents; failures always raise DeliveryError."""
        return self._request("/v1/incidents")

    @contextmanager
    def observe(self, *, deployment_id: str, agent_id: str, operation: str = "agent",
                synthetic: bool = False, **measurements):
        """Measure duration and classify exceptions without reading their messages.

        Does not infer semantic success. Emit explicit failure events for invalid
        outputs, incomplete tasks, loops, fallback, or other application findings.
        """
        start = time.perf_counter()
        outcome, failure_type = "success", None
        try:
            yield
        except BaseException as exc:
            outcome = "failure"
            failure_type = "timeout" if isinstance(exc, TimeoutError) else {
                "agent": "agent_error", "model": "model_error", "tool": "tool_error",
                "validation": "validation_failure",
            }.get(operation, "unknown")
            raise
        finally:
            try:
                self.emit(event(deployment_id=deployment_id, agent_id=agent_id,
                                operation=operation, outcome=outcome, failure_type=failure_type,
                                latency_ms=min((time.perf_counter() - start) * 1000, 86_400_000),
                                synthetic=synthetic, **measurements))
            except (SDKError, ValueError, TypeError) as exc:
                if not isinstance(exc, SDKError):
                    self._increment("dropped")
                # Never replace the original application exception.
                if outcome == "success" and self.failure_mode == "raise":
                    raise SDKError("observation could not be recorded") from None
