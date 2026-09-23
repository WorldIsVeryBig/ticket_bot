"""Ticket Plus CLI 串接測試。"""

import sys
import types

from click.testing import CliRunner

from ticket_bot import cli as cli_module
from ticket_bot.config import AppConfig, BrowserConfig, EventConfig, SessionConfig


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
