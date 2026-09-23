"""Tixcraft live header trace helpers."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from http.cookies import SimpleCookie
from pathlib import Path
from threading import Lock
from typing import Any, Iterable

from ticket_bot.config import TraceConfig

logger = logging.getLogger(__name__)

BROWSER_TRACE_URL_PATTERN = (
    r"tixcraft\.com/"
    r"(activity/(game|verify)/|ticket/(area|ticket|order|checkout|verify)|user/changeLanguage/)"
)

TRACE_URL_SEGMENTS = (
    "/activity/game/",
    "/activity/verify/",
    "/ticket/verify/",
    "/ticket/area/",
    "/ticket/ticket/",
    "/ticket/order",
    "/ticket/checkout",
    "/user/changeLanguage/",
)


def _parse_header_text(headers_text: str) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for line in headers_text.replace("\r", "").split("\n"):
        if not line or ":" not in line:
            continue
        name, value = line.split(":", 1)
        pairs.append((name.strip(), value.strip()))
    return pairs


def _coerce_header_pairs(headers: Any) -> list[tuple[str, str]]:
    if not headers:
        return []

    if isinstance(headers, str):
        return _parse_header_text(headers)

    if hasattr(headers, "multi_items"):
        return [
            (str(name), "" if value is None else str(value))
            for name, value in headers.multi_items()
        ]

    if isinstance(headers, dict):
        return [
            (str(name), "" if value is None else str(value))
            for name, value in headers.items()
        ]

    pairs: list[tuple[str, str]] = []
    for item in headers:
        if isinstance(item, dict):
            name = item.get("name")
            value = item.get("value")
        else:
            try:
                name, value = item
            except (TypeError, ValueError):
                continue
        if name is None:
            continue
        pairs.append((str(name), "" if value is None else str(value)))
    return pairs


def _header_values(pairs: Iterable[tuple[str, str]], target: str) -> list[str]:
    target = target.lower()
    return [value for name, value in pairs if name.lower() == target]


def _first_header(pairs: Iterable[tuple[str, str]], target: str) -> str:
    values = _header_values(pairs, target)
    return values[0] if values else ""


def _extract_set_cookie_names(pairs: list[tuple[str, str]]) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for raw_value in _header_values(pairs, "set-cookie"):
        cookie = SimpleCookie()
        parsed = False
        try:
            cookie.load(raw_value)
            for name in cookie.keys():
                if name not in seen:
                    names.append(name)
                    seen.add(name)
                    parsed = True
        except Exception:
            parsed = False

        if parsed:
            continue

        match = re.match(r"\s*([^=;,\s]+)=", raw_value)
        if match:
            name = match.group(1)
            if name not in seen:
                names.append(name)
                seen.add(name)
    return names


class TixcraftTraceLogger:
    """Write Cloudflare/session routing hints to a JSONL file."""

    def __init__(self, config: TraceConfig):
        self.enabled = config.enabled
        self.log_path = Path(config.log_path)
        self._lock = Lock()

    def should_trace_url(self, url: str) -> bool:
        return self.enabled and "tixcraft.com" in url and any(
            segment in url for segment in TRACE_URL_SEGMENTS
        )

    def trace_response(
        self,
        *,
        source: str,
        url: str,
        status_code: int,
        headers: Any,
        method: str = "",
        remote_ip: str = "",
        protocol: str = "",
        note: str = "",
    ) -> None:
        if not self.should_trace_url(url):
            return

        pairs = _coerce_header_pairs(headers)
        cookie_names = _extract_set_cookie_names(pairs)
        record: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "source": source,
            "method": method,
            "url": url,
            "status_code": status_code,
            "server": _first_header(pairs, "server"),
            "location": _first_header(pairs, "location"),
            "cf_ray": _first_header(pairs, "cf-ray"),
            "cf_cache_status": _first_header(pairs, "cf-cache-status"),
            "cf_mitigated": _first_header(pairs, "cf-mitigated"),
            "set_cookie_names": cookie_names,
            "has_cflb": "__cflb" in cookie_names,
            "has_cfwaitingroom": "__cfwaitingroom" in cookie_names,
        }
        if remote_ip:
            record["remote_ip"] = remote_ip
        if protocol:
            record["protocol"] = str(protocol)
        if note:
            record["note"] = note

        try:
            with self._lock:
                self.log_path.parent.mkdir(parents=True, exist_ok=True)
                with self.log_path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as exc:
            logger.warning("寫入 Tixcraft trace 失敗: %s", exc)
