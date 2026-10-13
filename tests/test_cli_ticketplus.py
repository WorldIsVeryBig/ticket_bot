"""Ticket Plus CLI 串接測試。"""

import sys
import types
import asyncio

import pytest
from click.testing import CliRunner

from ticket_bot import cli as cli_module
from ticket_bot.config import AppConfig, BrowserConfig, EventConfig, SessionConfig


@pytest.mark.asyncio
async def test_single_invocation_runs_ticketplus_countdown_action_once():
    calls = []

    async def action():
        calls.append("run")
        await asyncio.sleep(0)
        return True

    run_once = cli_module._single_invocation(action)
    results = await asyncio.gather(*(run_once() for _ in range(5)))

    assert calls == ["run"]
    assert results == [True, False, False, False, False]


def test_factory_builds_ticketplus_bot(monkeypatch):
    marker = object()
    monkeypatch.setattr(
        "ticket_bot.platforms.ticketplus.TicketPlusBot",
        lambda *args, **kwargs: marker,
    )
    cfg = AppConfig(browser=BrowserConfig())
    event = EventConfig(
        name="TP",
        platform="ticketplus",
        url="https://ticketplus.com.tw/activity/abc",
    )
    session = SessionConfig()

    assert cli_module._create_platform_bot(cfg, event, session, use_api=True) is marker


def test_login_help_lists_ticketplus():
    result = CliRunner().invoke(cli_module.cli, ["login", "--help"])
    assert result.exit_code == 0
    assert "ticketplus" in result.output


def _write_config(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        """events:
  - name: 遠大測試活動
    platform: ticketplus
    url: https://ticketplus.com.tw/activity/abc
    ticket_count: 2
    sale_time: "2026-10-12T14:50:00+08:00"
sessions:
  - name: default
    user_data_dir: ./profile
""",
        encoding="utf-8",
    )
    return path


def test_run_includes_ticketplus_target(tmp_path, monkeypatch):
    called = []

    async def fake_run(cfg, event, session, dry_run, use_api=False):
        called.append((event.platform, use_api))
        return False

    monkeypatch.setattr(cli_module, "_run_single_session", fake_run)
    config_path = _write_config(tmp_path)

    result = CliRunner().invoke(
        cli_module.cli,
        ["--config", str(config_path), "run", "--event", "遠大", "--api"],
    )

    assert result.exit_code == 0
    assert called == [("ticketplus", False)]


def test_watch_includes_ticketplus_target(tmp_path, monkeypatch):
    calls = []

    class FakeBot:
        last_success_info = ""

        async def watch(self, interval):
            calls.append(interval)
            return False

        async def close(self):
            return None

    monkeypatch.setattr(cli_module, "_create_platform_bot", lambda *args, **kwargs: FakeBot())
    fake_api = types.ModuleType("ticket_bot.platforms.tixcraft_api")
    fake_api.SessionFailoverRequiredError = RuntimeError
    monkeypatch.setitem(sys.modules, "ticket_bot.platforms.tixcraft_api", fake_api)
    config_path = _write_config(tmp_path)

    result = CliRunner().invoke(
        cli_module.cli,
        ["--config", str(config_path), "watch", "--event", "遠大", "--interval", "0"],
    )

    assert result.exit_code == 0
    assert calls == [0.0]


def test_countdown_includes_ticketplus_target(tmp_path, monkeypatch):
    config_path = _write_config(tmp_path)
    calls = []

    def fake_asyncio_run(coroutine):
        calls.append(coroutine)
        coroutine.close()

    monkeypatch.setattr(cli_module.asyncio, "run", fake_asyncio_run)

    result = CliRunner().invoke(
        cli_module.cli,
        ["--config", str(config_path), "countdown", "--event", "遠大"],
    )

    assert result.exit_code == 0
    assert len(calls) == 1
    assert "找不到有設定 sale_time" not in result.output


@pytest.mark.asyncio
async def test_ticketplus_countdown_prewarm_opens_event_on_reused_bot(monkeypatch):
    actions = []

    class FakePage:
        async def goto(self, url):
            actions.append(("goto", url))

    class FakeTicketPlusBot:
        async def _ensure_page(self):
            actions.append(("ensure_page", None))
            return FakePage()

    bot = FakeTicketPlusBot()
    monkeypatch.setattr(cli_module, "_create_platform_bot", lambda *args, **kwargs: bot)
    cfg = AppConfig(browser=BrowserConfig())
    event = EventConfig(
        name="TP",
        platform="ticketplus",
        url="https://ticketplus.com.tw/activity/abc",
    )

    warmed_bot = await cli_module._prewarm_countdown_bot(
        cfg, event, SessionConfig()
    )

    assert warmed_bot is bot
    assert actions == [
        ("ensure_page", None),
        ("goto", "https://ticketplus.com.tw/activity/abc"),
    ]
