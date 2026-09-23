"""Redacted XHR/fetch recorder for target API sites."""

from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import re
import time
from collections.abc import Coroutine, Mapping, Sequence
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path
from types import MappingProxyType
from typing import Any, Protocol
from urllib.parse import parse_qs, parse_qsl, urlencode, urlsplit, urlunsplit

REDACTED = "<redacted>"
DEFAULT_MAX_BODY_BYTES = 262_144
_SENSITIVE_NAMES = (
    "authorization",
    "cookie",
    "csrf",
    "token",
    "secret",
    "password",
    "phone",
    "email",
    "identity",
)
_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)[\"']?(?:authorization|cookie|csrf|token|secret|password|phone|email|identity)"
    r"[a-z0-9_-]*[\"']?\s*[:=]"
)
_QUEUE_TOKEN_PATH = re.compile(r"^(/api/queue/)(?!join(?:/|$))[^/]+")
_HOST_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_CAPTURABLE_RESOURCE_TYPES = frozenset({"xhr", "fetch"})


def _is_sensitive_name(name: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", name.lower())
    return any(item in normalized for item in _SENSITIVE_NAMES)


def _validate_test_hostname(hostname: str | None) -> str:
    normalized = (hostname or "").lower()
    labels = normalized.split(".")
    if (
        not normalized
        or any(not _HOST_LABEL.fullmatch(label) for label in labels)
    ):
        raise ValueError("Invalid capture target hostname")
    return normalized


def _origin_parts(url: str) -> tuple[str, str, int]:
    parts = urlsplit(url)
    if parts.scheme.lower() not in {"http", "https"}:
        raise ValueError("Capture target must use http or https")
    if parts.username is not None or parts.password is not None:
        raise ValueError("Capture target must not contain credentials")
    hostname = _validate_test_hostname(parts.hostname)
    try:
        port = parts.port
    except ValueError as exc:
        raise ValueError("Capture target contains an invalid port") from exc
    effective_port = port or (443 if parts.scheme.lower() == "https" else 80)
    return parts.scheme.lower(), hostname, effective_port


def validate_capture_url(url: str) -> str:
    """Validate a target URL and return its normalized origin."""

    scheme, hostname, effective_port = _origin_parts(url)
    default_port = 443 if scheme == "https" else 80
    port_suffix = "" if effective_port == default_port else f":{effective_port}"
    return f"{scheme}://{hostname}{port_suffix}"


def is_capturable(base_origin: str, url: str, resource_type: str) -> bool:
    """Return whether a browser event belongs to the configured target API."""

    if resource_type.lower() not in _CAPTURABLE_RESOURCE_TYPES:
        return False
    try:
        base_scheme, base_host, _ = _origin_parts(base_origin)
        scheme, host, _ = _origin_parts(url)
        if scheme != base_scheme:
            return False
        base_domain = base_host.removeprefix("www.")
        host_domain = host.removeprefix("www.")
        return (
            host_domain == base_domain
            or host_domain.endswith("." + base_domain)
            or base_domain.endswith("." + host_domain)
        )
    except (TypeError, ValueError):
        return False


def redact_value(value: object, key: str = "") -> object:
    """Recursively replace values whose field name is security-sensitive."""

    if key and _is_sensitive_name(key):
        return REDACTED
    if isinstance(value, Mapping):
        return {
            str(item_key): redact_value(item_value, str(item_key))
            for item_key, item_value in value.items()
        }
    if isinstance(value, tuple):
        return [redact_value(item) for item in value]
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    return value


def _redact_text(value: str) -> str:
    if _SENSITIVE_ASSIGNMENT.search(value):
        return REDACTED
    return value


def redact_path(path: str) -> str:
    """Replace synthetic queue-token path segments with a stable template."""

    return _QUEUE_TOKEN_PATH.sub(r"\1{queue_token}", path)


def redact_url(url: str) -> str:
    """Redact sensitive query values while retaining URL structure."""

    parts = urlsplit(url)
    redacted_query = [
        (key, REDACTED if _is_sensitive_name(key) else value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
    ]
    fragment = REDACTED if parts.fragment else ""
    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            redact_path(parts.path),
            urlencode(redacted_query, doseq=True),
            fragment,
        )
    )


def _decode_body(data: bytes | str) -> tuple[str, int]:
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace"), len(data)
    encoded = data.encode("utf-8")
    return data, len(encoded)


def _compact_query(values: Mapping[str, list[str]]) -> dict[str, object]:
    return {
        key: items[0] if len(items) == 1 else items
        for key, items in values.items()
    }


def parse_body(
    data: bytes | str | None,
    content_type: str,
    max_bytes: int = DEFAULT_MAX_BODY_BYTES,
) -> object:
    """Parse a bounded body into JSON-safe data and redact sensitive fields."""

    if data is None:
        return None
    text, size = _decode_body(data)
    if size > max_bytes:
        return {"omitted": True, "reason": "body_too_large", "size": size}

    media_type = content_type.split(";", 1)[0].strip().lower()
    is_json = media_type == "application/json" or media_type.endswith("+json")
    if is_json:
        try:
            return redact_value(json.loads(text))
        except (RecursionError, ValueError) as exc:
            if isinstance(exc, json.JSONDecodeError):
                return _redact_text(text)
            return {
                "omitted": True,
                "reason": "json_complexity_limit",
                "size": size,
            }
        except UnicodeDecodeError:
            return _redact_text(text)
    if media_type == "application/x-www-form-urlencoded":
        parsed = _compact_query(parse_qs(text, keep_blank_values=True))
        return redact_value(parsed)
    if media_type.startswith("text/") or not media_type:
        return _redact_text(text)
    return {"omitted": True, "reason": "binary_content", "size": size}


def schema_of(value: object) -> object:
    """Return a deterministic recursive type shape for a JSON-compatible value."""

    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, Mapping):
        return {key: schema_of(value[key]) for key in sorted(value)}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        if not value:
            return []
        merged = schema_of(value[0])
        for item in value[1:]:
            merged = _merge_schema(merged, schema_of(item))
        return [merged]
    return type(value).__name__


def _merge_schema(left: object, right: object) -> object:
    if left == right:
        return left
    choices: list[object] = []
    for candidate in (left, right):
        if isinstance(candidate, Mapping) and set(candidate) == {"one_of"}:
            choices.extend(candidate["one_of"])
        else:
            choices.append(candidate)
    unique = {json.dumps(item, sort_keys=True): item for item in choices}
    return {"one_of": [unique[key] for key in sorted(unique)]}


def _redact_headers(headers: Mapping[str, str]) -> dict[str, object]:
    normalized = {str(key).lower(): str(value) for key, value in headers.items()}
    return redact_value(normalized)  # type: ignore[return-value]


class ApiRecorder:
    """Pair Patchright request/response objects and persist safe observations."""

    def __init__(
        self,
        base_url: str,
        output_path: str | Path,
        summary_path: str | Path,
        max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
    ) -> None:
        self.base_origin = validate_capture_url(base_url)
        self.output_path = Path(output_path)
        self.summary_path = Path(summary_path)
        self.max_body_bytes = max_body_bytes
        self._next_id = itertools.count(1)
        self._requests: dict[int, dict[str, Any]] = {}
        self._records: list[dict[str, Any]] = []
        self._pending: set[asyncio.Task[Any]] = set()

    @property
    def records(self) -> tuple[Mapping[str, Any], ...]:
        return tuple(MappingProxyType(record) for record in self._records)

    def schedule(self, coroutine: Coroutine[Any, Any, object]) -> asyncio.Task[Any]:
        task = asyncio.create_task(coroutine)
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)
        return task

    async def drain(self) -> None:
        while self._pending:
            await asyncio.gather(*tuple(self._pending))

    async def record_request(self, request: Any, page_url: str) -> None:
        if not is_capturable(self.base_origin, request.url, request.resource_type):
            return

        headers = await request.all_headers()
        normalized_headers = _redact_headers(headers)
        content_type = str(headers.get("content-type", ""))
        parts = urlsplit(request.url)
        parsed_query = _compact_query(parse_qs(parts.query, keep_blank_values=True))
        request_number = next(self._next_id)
        self._requests[id(request)] = {
            "request_id": f"req-{request_number:06d}",
            "timestamp": datetime.now(UTC).isoformat(),
            "page_url": redact_url(page_url),
            "request": {
                "method": request.method.upper(),
                "url": redact_url(request.url),
                "path": redact_path(parts.path or "/"),
                "query": redact_value(parsed_query),
                "headers": normalized_headers,
                "content_type": content_type,
                "body": parse_body(
                    request.post_data,
                    content_type,
                    self.max_body_bytes,
                ),
            },
            "_started": time.monotonic(),
        }

    async def record_response(self, response: Any) -> None:
        request_state = self._requests.pop(id(response.request), None)
        if request_state is None:
            return

        headers = await response.all_headers()
        content_type = str(headers.get("content-type", ""))
        response_data: dict[str, Any] = {
            "status": response.status,
            "headers": _redact_headers(headers),
            "content_type": content_type,
        }
        try:
            body = await response.body()
            response_data["body"] = parse_body(
                body,
                content_type,
                self.max_body_bytes,
            )
        except Exception as exc:  # noqa: BLE001 - browser backends raise varied errors.
            response_data["body_error"] = _redact_text(str(exc))[:500]

        started = request_state.pop("_started")
        request_state["response"] = response_data
        request_state["elapsed_ms"] = round((time.monotonic() - started) * 1000, 3)
        self._records.append(request_state)

    def write_outputs(self) -> tuple[Path, Path]:
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self.summary_path.parent.mkdir(parents=True, exist_ok=True)

        with self.output_path.open("w", encoding="utf-8") as output_file:
            for record in self._records:
                output_file.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
                output_file.write("\n")

        summary = self._build_summary()
        self.summary_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return self.output_path, self.summary_path

    def _build_summary(self) -> dict[str, object]:
        endpoints: dict[str, dict[str, Any]] = {}
        for record in self._records:
            request = record["request"]
            response = record["response"]
            endpoint_key = f"{request['method']} {request['path']}"
            endpoint = endpoints.setdefault(
                endpoint_key,
                {
                    "sample_count": 0,
                    "status_codes": set(),
                    "request_schema": None,
                    "response_schema": None,
                },
            )
            endpoint["sample_count"] += 1
            endpoint["status_codes"].add(response["status"])
            request_schema = schema_of(request.get("body"))
            response_schema = schema_of(response.get("body"))
            endpoint["request_schema"] = _merge_schema(
                endpoint["request_schema"], request_schema
            ) if endpoint["request_schema"] is not None else request_schema
            endpoint["response_schema"] = _merge_schema(
                endpoint["response_schema"], response_schema
            ) if endpoint["response_schema"] is not None else response_schema

        serializable_endpoints = {
            key: {
                **value,
                "status_codes": sorted(value["status_codes"]),
            }
            for key, value in sorted(endpoints.items())
        }
        return {
            "base_origin": self.base_origin,
            "record_count": len(self._records),
            "endpoints": serializable_endpoints,
        }


class BrowserRuntime(Protocol):
    async def launch_persistent_context(self, **options: object) -> Any: ...

    async def close(self) -> None: ...


class PatchrightRuntime:
    """Lazy Patchright adapter so unit tests do not launch a browser."""

    def __init__(self) -> None:
        self._playwright: Any = None

    async def launch_persistent_context(self, **options: object) -> Any:
        try:
            patchright_api = import_module("patchright.async_api")
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "Patchright is not installed; run: python -m pip install patchright"
            ) from exc

        manager = patchright_api.async_playwright()
        self._playwright = await manager.start()
        return await self._playwright.chromium.launch_persistent_context(**options)

    async def close(self) -> None:
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None


async def _wait_for_enter(prompt: str) -> None:
    await asyncio.to_thread(input, prompt)


async def run_capture(
    *,
    url: str,
    profile: str | Path,
    output_path: str | Path,
    summary_path: str | Path,
    ignore_https_errors: bool = False,
    headless: bool = False,
    executable_path: str | None = None,
    runtime: BrowserRuntime | None = None,
    wait_for_stop: Any = None,
) -> tuple[Path, Path]:
    """Open a target URL, record API events, and persist redacted outputs."""

    validate_capture_url(url)
    profile_path = Path(profile)
    profile_path.mkdir(parents=True, exist_ok=True)
    recorder = ApiRecorder(url, output_path, summary_path)
    browser_runtime = runtime or PatchrightRuntime()
    stop_callback = wait_for_stop or _wait_for_enter
    context = None
    request_tasks: dict[int, asyncio.Task[Any]] = {}

    launch_options: dict[str, object] = {
        "user_data_dir": str(profile_path),
        "headless": headless,
        "locale": "zh-TW",
        "viewport": None,
        "ignore_https_errors": ignore_https_errors,
        # "service_workers": "block",
    }
    if executable_path:
        launch_options["executable_path"] = executable_path

    try:
        context = await browser_runtime.launch_persistent_context(**launch_options)
        page = context.pages[0] if context.pages else await context.new_page()

        def handle_request(request: Any) -> None:
            task = recorder.schedule(
                recorder.record_request(request, page_url=page.url)
            )
            request_tasks[id(request)] = task

        def handle_response(response: Any) -> None:
            async def record_after_request() -> None:
                request_task = request_tasks.pop(id(response.request), None)
                if request_task is not None:
                    await request_task
                await recorder.record_response(response)

            recorder.schedule(record_after_request())

        page.on("request", handle_request)
        page.on("response", handle_response)
        await page.goto(url, wait_until="domcontentloaded")
        await stop_callback("Complete the browser flow, then press Enter to save: ")
        await recorder.drain()
        return recorder.write_outputs()
    finally:
        await recorder.drain()
        if context is not None:
            await context.close()
        await browser_runtime.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Record API events for a target site")
    parser.add_argument("--url", required=True, help="Target activity URL")
    parser.add_argument("--profile", type=Path, default=Path("patchright_profile"))
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("logs/ticketplus_api_capture.jsonl"),
    )
    parser.add_argument(
        "--summary",
        type=Path,
        default=Path("logs/ticketplus_api_summary.json"),
    )
    parser.add_argument("--executable-path")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--ignore-https-errors", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    asyncio.run(
        run_capture(
            url=args.url,
            profile=args.profile,
            output_path=args.output,
            summary_path=args.summary,
            executable_path=args.executable_path,
            headless=args.headless,
            ignore_https_errors=args.ignore_https_errors,
        )
    )


if __name__ == "__main__":
    main()