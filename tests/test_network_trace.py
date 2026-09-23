"""Tixcraft live trace 測試"""

import json

from ticket_bot.config import TraceConfig
from ticket_bot.network_trace import TixcraftTraceLogger


def test_trace_logger_records_cloudflare_headers(tmp_path):
    log_path = tmp_path / "trace.jsonl"
    trace = TixcraftTraceLogger(
        TraceConfig(enabled=True, log_path=str(log_path))
    )

    trace.trace_response(
        source="api",
        method="GET",
        url="https://tixcraft.com/ticket/order/26_test/123",
        status_code=302,
        headers=[
            ("CF-Ray", "abc123-TPE"),
            ("Location", "/ticket/checkout/26_test/123"),
            ("Set-Cookie", "__cflb=secret; Path=/; HttpOnly"),
            ("Set-Cookie", "__cfwaitingroom=queued; Path=/"),
            ("Set-Cookie", "eps_sid=hidden; Path=/"),
        ],
        remote_ip="1.2.3.4",
        protocol="h2",
        note="follow_redirect",
    )

    raw = log_path.read_text(encoding="utf-8")
    record = json.loads(raw.strip())

    assert record["cf_ray"] == "abc123-TPE"
    assert record["location"] == "/ticket/checkout/26_test/123"
    assert record["set_cookie_names"] == ["__cflb", "__cfwaitingroom", "eps_sid"]
    assert record["has_cflb"] is True
    assert record["has_cfwaitingroom"] is True
    assert record["remote_ip"] == "1.2.3.4"
    assert "secret" not in raw
    assert "hidden" not in raw


def test_trace_logger_parses_raw_header_text(tmp_path):
    log_path = tmp_path / "trace.jsonl"
    trace = TixcraftTraceLogger(
        TraceConfig(enabled=True, log_path=str(log_path))
    )

    trace.trace_response(
        source="browser",
        url="https://tixcraft.com/ticket/area/26_test/123",
        method="GET",
        status_code=200,
        headers=(
            "HTTP/2 200\r\n"
            "server: cloudflare\r\n"
            "cf-ray: def456-NRT\r\n"
            "set-cookie: __cflb=sticky; Path=/\r\n"
        ),
        protocol="h2",
    )

    record = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert record["server"] == "cloudflare"
    assert record["cf_ray"] == "def456-NRT"
    assert record["set_cookie_names"] == ["__cflb"]


def test_trace_logger_skips_non_target_url(tmp_path):
    log_path = tmp_path / "trace.jsonl"
    trace = TixcraftTraceLogger(
        TraceConfig(enabled=True, log_path=str(log_path))
    )

    trace.trace_response(
        source="api",
        method="GET",
        url="https://example.com/path",
        status_code=200,
        headers=[],
    )

    assert log_path.exists() is False
