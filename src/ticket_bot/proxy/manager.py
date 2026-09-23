"""Proxy 輪替管理"""

from __future__ import annotations

import itertools
import logging
import uuid

from ticket_bot.config import ProxyConfig

logger = logging.getLogger(__name__)


class ProxyManager:
    """Round-robin proxy 輪替器"""

    def __init__(self, config: ProxyConfig):
        self.config = config
        self.servers = config.servers or []
        if self.servers and self.config.rotate:
            cycle = getattr(self.config, "_shared_cycle", None)
            cycle_servers = getattr(self.config, "_shared_cycle_servers", None)
            if cycle is None or cycle_servers != tuple(self.servers):
                cycle = itertools.cycle(self.servers)
                self.config._shared_cycle = cycle
                self.config._shared_cycle_servers = tuple(self.servers)
            self._cycle = cycle
        else:
            self._cycle = itertools.cycle(self.servers) if self.servers else None

    @property
    def available(self) -> bool:
        return self.config.enabled and bool(self.servers)

    def next(self) -> str | None:
        """取得下一個 proxy server URL"""
        if not self.available or self._cycle is None:
            return None
        if self.config.rotate:
            server = next(self._cycle)
        else:
            server = self.servers[0]
            
        # 支援 Residential Proxy 動態 Session ID
        # 範例: http://user-session-{session_id}:pass@proxy.com:8080
        if "{session_id}" in server:
            session_id = uuid.uuid4().hex[:8]
            server = server.replace("{session_id}", session_id)
            
        logger.debug("使用 proxy: %s", server.split("@")[-1] if "@" in server else server)
        return server

    def get_browser_arg(self) -> str | None:
        """取得 --proxy-server Chrome 啟動參數"""
        server = self.next()
        if server:
            return f"--proxy-server={server}"
        return None

    def get_playwright_config(self) -> dict | None:
        """取得 Playwright proxy config dict"""
        server = self.next()
        if not server:
            return None
        # 解析 http://user:pass@host:port 格式
        config: dict = {"server": server}
        if "@" in server:
            prefix = server.split("://")[0] if "://" in server else "http"
            rest = server.split("://", 1)[-1]
            auth, host = rest.rsplit("@", 1)
            user, password = auth.split(":", 1)
            config["server"] = f"{prefix}://{host}"
            config["username"] = user
            config["password"] = password
        return config
