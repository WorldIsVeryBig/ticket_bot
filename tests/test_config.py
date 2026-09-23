"""config 模組測試"""

import textwrap

import pytest

from ticket_bot.config import (
    SessionConfig,
    load_config,
)


@pytest.fixture()
def config_dir(tmp_path):
    """建立含 config.yaml + .env 的臨時目錄"""
    yaml_content = textwrap.dedent("""\
        events:
          - name: "測試演唱會"
            platform: tixcraft
            url: "https://tixcraft.com/activity/game/test"
            ticket_count: 4
            date_keyword: "03/25"
            area_keyword: "A區"
            sale_time: "2026-04-15T12:00:00+08:00"

        browser:
          engine: playwright
          headless: true
          user_data_dir: "./test_profile"
          pre_warm: false
          lang: "en-US"

        captcha:
          engine: ddddocr
          beta_model: false
          char_ranges: 0
          confidence_threshold: 0.8
          max_attempts: 3
          preprocess: false

        kktix:
          enabled: true
          contact_name: "王小明"
          contact_email: "demo@example.com"
          contact_phone: "0912345678"
          contact_gender: "male"
          contact_birth_date: "1990-01-02"
          contact_region: "taipei"
          attendee_names:
            - "王小明"
            - "王小華"
          attendee_phones:
            - "0912345678"
            - "0922333444"
          attendee_id_numbers:
            - "A123456789"
            - "B223456789"
          agree_real_name: true
          display_public_attendance: false
          join_organizer_fan: true

        notifications:
          telegram:
            enabled: true
            chat_id: "12345"
          discord:
            enabled: true

        proxy:
          enabled: true
          rotate: false
          servers:
            - "http://proxy1:8080"
            - "http://proxy2:8080"

        sessions:
          - name: "帳號A"
            user_data_dir: "./profile_a"
            proxy_server: "http://pa:8080"
            cookie_file: "./cookies_a.json"
          - name: "帳號B"
            user_data_dir: "./profile_b"
    """)

    env_content = textwrap.dedent("""\
        TELEGRAM_BOT_TOKEN=test-bot-token
        DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/test
        TICKETMASTER_API_KEY=tm-test-key
    """)

    (tmp_path / "config.yaml").write_text(yaml_content, encoding="utf-8")
    (tmp_path / ".env").write_text(env_content, encoding="utf-8")
    return tmp_path


def test_load_config_full(config_dir):
    """完整設定檔載入"""
    cfg = load_config(
        str(config_dir / "config.yaml"),
        str(config_dir / ".env"),
    )

    # events
    assert len(cfg.events) == 1
    ev = cfg.events[0]
    assert ev.name == "測試演唱會"
    assert ev.platform == "tixcraft"
    assert ev.ticket_count == 4
    assert ev.date_keyword == "03/25"
    assert ev.area_keyword == "A區"

    # browser
    assert cfg.browser.engine == "playwright"
    assert cfg.browser.headless is True
    assert cfg.browser.user_data_dir == "./test_profile"
    assert cfg.browser.pre_warm is False
    assert cfg.browser.lang == "en-US"

    # captcha
    assert cfg.captcha.beta_model is False
    assert cfg.captcha.confidence_threshold == 0.8
    assert cfg.captcha.max_attempts == 3

    # kktix autofill
    assert cfg.kktix.enabled is True
    assert cfg.kktix.contact_name == "王小明"
    assert cfg.kktix.contact_email == "demo@example.com"
    assert cfg.kktix.contact_phone == "0912345678"
    assert cfg.kktix.contact_gender == "male"
    assert cfg.kktix.contact_birth_date == "1990-01-02"
    assert cfg.kktix.contact_region == "taipei"
    assert cfg.kktix.attendee_names == ["王小明", "王小華"]
    assert cfg.kktix.attendee_phones == ["0912345678", "0922333444"]
    assert cfg.kktix.attendee_id_numbers == ["A123456789", "B223456789"]
    assert cfg.kktix.join_organizer_fan is True

    # notifications (合併 .env)
    assert cfg.notifications.telegram.enabled is True
    assert cfg.notifications.telegram.bot_token == "test-bot-token"
    assert cfg.notifications.telegram.chat_id == "12345"
    assert cfg.notifications.discord.enabled is True
    assert cfg.notifications.discord.webhook_url == "https://discord.com/api/webhooks/test"

    # proxy
    assert cfg.proxy.enabled is True
    assert cfg.proxy.rotate is False
    assert len(cfg.proxy.servers) == 2

    # sessions
    assert len(cfg.sessions) == 2
    assert cfg.sessions[0].name == "帳號A"
    assert cfg.sessions[0].user_data_dir == "./profile_a"
    assert cfg.sessions[0].proxy_server == "http://pa:8080"
    assert cfg.sessions[0].cookie_file == "./cookies_a.json"
    assert cfg.sessions[1].name == "帳號B"
    assert cfg.sessions[1].proxy_server == ""
    assert cfg.sessions[1].cookie_file == ""

    # ticketmaster
    assert cfg.ticketmaster_api_key == "tm-test-key"


def test_load_config_defaults(tmp_path):
    """最小設定檔 → 使用預設值"""
    (tmp_path / "config.yaml").write_text("events: []\n", encoding="utf-8")
    (tmp_path / ".env").write_text("", encoding="utf-8")

    cfg = load_config(str(tmp_path / "config.yaml"), str(tmp_path / ".env"))

    assert cfg.events == []
    assert cfg.deployment.profile == ""
    assert cfg.browser.engine == "nodriver"
    assert cfg.browser.headless is False
    assert cfg.captcha.engine == "ddddocr"
    assert cfg.kktix.enabled is False
    assert cfg.notifications.telegram.enabled is False
    assert cfg.proxy.enabled is False
    # 無 sessions 設定 → 自動建立一個預設 session
    assert len(cfg.sessions) == 1
    assert cfg.sessions[0].name == "default"
    assert cfg.sessions[0].user_data_dir == cfg.browser.user_data_dir


def test_ticketplus_event_preferences_load(tmp_path):
    """Ticket Plus 可用多個票區順位與最高票價。"""
    (tmp_path / "config.yaml").write_text(
        textwrap.dedent("""\
            events:
              - name: "遠大測試活動"
                platform: ticketplus
                url: "https://ticketplus.com.tw/activity/example"
                ticket_count: 2
                area_keyword: "S區"
                area_keywords:
                  - "S區"
                  - "一般區"
                max_price: 4000
        """),
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text("", encoding="utf-8")

    event = load_config(
        str(tmp_path / "config.yaml"),
        str(tmp_path / ".env"),
    ).events[0]

    assert event.area_keyword == "S區"
    assert event.area_keywords == ["S區", "一般區"]
    assert event.max_price == 4000


def test_old_event_yaml_gets_ticketplus_defaults(tmp_path):
    """舊版 YAML 不寫新欄位時仍可正常載入。"""
    (tmp_path / "config.yaml").write_text(
        textwrap.dedent("""\
            events:
              - name: "舊設定"
                platform: tixcraft
                url: "https://tixcraft.com/activity/game/example"
                area_keyword: "A區"
        """),
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text("", encoding="utf-8")

    event = load_config(
        str(tmp_path / "config.yaml"),
        str(tmp_path / ".env"),
    ).events[0]

    assert event.area_keyword == "A區"
    assert event.area_keywords == []
    assert event.max_price is None


def test_load_config_captcha_collect_env(tmp_path, monkeypatch):
    """captcha 收集可由環境變數啟用/覆蓋"""
    (tmp_path / "config.yaml").write_text("events: []\n", encoding="utf-8")
    (tmp_path / ".env").write_text("", encoding="utf-8")

    monkeypatch.setenv("CAPTCHA_COLLECT_ENABLED", "true")
    cfg = load_config(str(tmp_path / "config.yaml"), str(tmp_path / ".env"))
    assert cfg.captcha.collect_dir == "./captcha_samples"

    monkeypatch.setenv("CAPTCHA_COLLECT_DIR", "/tmp/captcha_samples")
    cfg = load_config(str(tmp_path / "config.yaml"), str(tmp_path / ".env"))
    assert cfg.captcha.collect_dir == "/tmp/captcha_samples"


def test_load_config_trace_env(tmp_path, monkeypatch):
    """trace 可由環境變數啟用/覆蓋"""
    (tmp_path / "config.yaml").write_text("events: []\n", encoding="utf-8")
    (tmp_path / ".env").write_text("", encoding="utf-8")

    monkeypatch.setenv("TIXCRAFT_TRACE_HEADERS", "true")
    monkeypatch.setenv("TIXCRAFT_TRACE_LOG_PATH", "/tmp/live_trace.jsonl")

    cfg = load_config(str(tmp_path / "config.yaml"), str(tmp_path / ".env"))

    assert cfg.trace.enabled is True
    assert cfg.trace.log_path == "/tmp/live_trace.jsonl"


def test_load_config_proxy_env(tmp_path, monkeypatch):
    """proxy 可由環境變數啟用並覆蓋 server/rotate"""
    (tmp_path / "config.yaml").write_text("events: []\n", encoding="utf-8")
    (tmp_path / ".env").write_text("", encoding="utf-8")

    monkeypatch.setenv("PROXY_ENABLED", "true")
    monkeypatch.setenv("PROXY_ROTATE", "false")
    monkeypatch.setenv(
        "PROXY_SERVERS",
        "http://user:pass@proxy1:8080,\nhttp://user:pass@proxy2:8080",
    )

    cfg = load_config(str(tmp_path / "config.yaml"), str(tmp_path / ".env"))

    assert cfg.proxy.enabled is True
    assert cfg.proxy.rotate is False
    assert cfg.proxy.servers == [
        "http://user:pass@proxy1:8080",
        "http://user:pass@proxy2:8080",
    ]


def test_load_config_deployment_profile_defaults(tmp_path):
    """deployment profile 會提供對應部署環境的 baseline"""
    (tmp_path / "config.yaml").write_text(
        "deployment:\n  profile: aws_tokyo\nevents: []\n",
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text("", encoding="utf-8")

    cfg = load_config(str(tmp_path / "config.yaml"), str(tmp_path / ".env"))

    assert cfg.deployment.profile == "aws_tokyo"
    assert cfg.browser.engine == "playwright"
    assert cfg.browser.headless is True
    assert cfg.browser.executable_path == "/usr/bin/chromium"
    assert cfg.browser.user_data_dir == "./chrome_profile_node_1"
    assert cfg.captcha.custom_model_path == "model/captcha_model.onnx"
    assert cfg.notifications.telegram.enabled is False
    assert cfg.notifications.discord.enabled is False
    assert cfg.trace.log_path == "./logs/tixcraft_trace_aws_tokyo.jsonl"


def test_load_config_deployment_profile_yaml_override(tmp_path):
    """明確寫在 YAML 的值要蓋過 profile 預設"""
    (tmp_path / "config.yaml").write_text(
        textwrap.dedent("""\
            deployment:
              profile: gcp_taiwan

            events: []

            browser:
              headless: false
              engine: nodriver

            notifications:
              telegram:
                enabled: false
        """),
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text("", encoding="utf-8")

    cfg = load_config(str(tmp_path / "config.yaml"), str(tmp_path / ".env"))

    assert cfg.deployment.profile == "gcp_taiwan"
    assert cfg.browser.headless is False
    assert cfg.browser.engine == "nodriver"
    assert cfg.trace.log_path == "./logs/tixcraft_trace_cloud.jsonl"
    assert cfg.notifications.telegram.enabled is False
    assert cfg.notifications.discord.enabled is True


def test_load_config_deployment_profile_env_override(tmp_path, monkeypatch):
    """DEPLOYMENT_PROFILE 環境變數優先於 YAML profile"""
    (tmp_path / "config.yaml").write_text(
        "deployment:\n  profile: aws_tokyo\nevents: []\n",
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text("", encoding="utf-8")
    monkeypatch.setenv("DEPLOYMENT_PROFILE", "local")

    cfg = load_config(str(tmp_path / "config.yaml"), str(tmp_path / ".env"))

    assert cfg.deployment.profile == "local_desktop"
    assert cfg.browser.engine == "nodriver"
    assert cfg.browser.headless is False
    assert cfg.browser.executable_path == ""
    assert cfg.trace.log_path == "./logs/tixcraft_trace_local.jsonl"
    assert cfg.captcha.collect_dir == "./captcha_samples"


def test_load_config_missing_file():
    """設定檔不存在 → FileNotFoundError"""
    with pytest.raises(FileNotFoundError, match="找不到設定檔"):
        load_config("/nonexistent/path/config.yaml")


def test_session_config_defaults():
    """SessionConfig 預設值"""
    s = SessionConfig()
    assert s.name == "default"
    assert s.user_data_dir == "./chrome_profile"
    assert s.proxy_server == ""
    assert s.cookie_file == ""
