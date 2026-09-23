"""Discord Bot — 透過 Discord 頻道指令控制搶票機器人"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime

import discord
from discord.ext import commands
from dotenv import load_dotenv

from ticket_bot.config import load_config

logger = logging.getLogger(__name__)

# ── Bot 設定 ─────────────────────────────────────────────────
COMMAND_PREFIX = "!"
BOT_STATUS = "搶票待命中 | !help"


class TicketBotCog(commands.Cog, name="搶票指令"):
    """搶票相關的 Discord 指令"""

    def __init__(self, bot: commands.Bot, config_path: str = "config.yaml"):
        self.bot = bot
        self.config_path = config_path
        self._active_task: asyncio.Task | None = None
        self._active_bot = None  # TixcraftBot instance
        self._status: str = "idle"  # idle / running / watching

    def _load_cfg(self):
        return load_config(self.config_path)

    def _get_event(self, cfg, name: str | None = None):
        targets = [e for e in cfg.events if e.platform == "tixcraft"]
        if name:
            targets = [e for e in targets if name in e.name]
        return targets[0] if targets else None

    async def _send_embed(self, ctx, title: str, desc: str, color: int = 0x03B2F8):
        embed = discord.Embed(title=title, description=desc, color=color)
        embed.set_footer(text=f"Ticket Bot • {datetime.now().strftime('%H:%M:%S')}")
        await ctx.send(embed=embed)

    async def _send_success(self, ctx, title: str, desc: str):
        await self._send_embed(ctx, title, desc, color=0x00C851)

    async def _send_error(self, ctx, title: str, desc: str):
        await self._send_embed(ctx, title, desc, color=0xFF4444)

    # ── !status ──────────────────────────────────────────────

    @commands.is_owner()
    @commands.command(name="status", aliases=["s"])
    async def status(self, ctx):
        """查看目前搶票狀態"""
        cfg = self._load_cfg()
        ev = self._get_event(cfg)

        status_emoji = {"idle": "💤", "running": "🚀", "watching": "👀"}
        status_text = {"idle": "待命中", "running": "搶票中", "watching": "監測中"}

        desc = f"**狀態：** {status_emoji.get(self._status, '❓')} {status_text.get(self._status, self._status)}\n"
        if ev:
            desc += f"**活動：** {ev.name}\n"
            desc += f"**日期：** {ev.date_keyword or '第一個可用'}\n"
            desc += f"**區域：** {ev.area_keyword or '第一個可用'}\n"
            desc += f"**票數：** {ev.ticket_count}\n"
            desc += f"**引擎：** {cfg.browser.engine}"
        else:
            desc += "未設定活動"

        await self._send_embed(ctx, "Ticket Bot 狀態", desc)

    # ── !run ─────────────────────────────────────────────────

    @commands.is_owner()
    @commands.command(name="run", aliases=["r"])
    async def run_ticket(self, ctx, *, args: str = ""):
        """啟動搶票 — `!run [活動名稱]`"""
        if self._status != "idle":
            await self._send_error(ctx, "無法啟動", f"目前正在 **{self._status}**，請先 `!stop`")
            return

        cfg = self._load_cfg()
        ev = self._get_event(cfg, args.strip() or None)
        if not ev:
            await self._send_error(ctx, "找不到活動", "請確認 `config.yaml` 已設定 tixcraft 活動")
            return

        self._status = "running"
        await self._send_embed(
            ctx, "開始搶票 🚀",
            f"**活動：** {ev.name}\n**日期：** {ev.date_keyword or '第一個可用'}\n"
            f"**區域：** {ev.area_keyword or '第一個可用'}\n**票數：** {ev.ticket_count}",
        )

        async def _do_run():
            sess = cfg.sessions[0]
            if cfg.browser.api_mode != "off":
                from ticket_bot.platforms.tixcraft_api import TixcraftApiBot
                bot = TixcraftApiBot(cfg, ev, session=sess)
            else:
                from ticket_bot.platforms.tixcraft import TixcraftBot
                bot = TixcraftBot(cfg, ev, session=sess)
            self._active_bot = bot
            try:
                success = await bot.run()
                if success:
                    await self._send_success(
                        ctx, "搶票成功！ 🎉",
                        f"**{ev.name}**\n請在瀏覽器中 **10 分鐘內完成付款**！",
                    )
                    await asyncio.sleep(600)
                else:
                    await self._send_error(ctx, "搶票失敗", f"**{ev.name}** 未能成功進入結帳頁面")
            except asyncio.CancelledError:
                await self._send_embed(ctx, "搶票已停止", "使用者手動停止")
            except Exception as e:
                logger.exception("搶票錯誤")
                await self._send_error(ctx, "搶票錯誤", f"```{e}```")
            finally:
                await bot.close()
                self._active_bot = None
                self._status = "idle"

        self._active_task = asyncio.create_task(_do_run())

    # ── !watch ───────────────────────────────────────────────

    @commands.is_owner()
    @commands.command(name="watch", aliases=["w"])
    async def watch_ticket(self, ctx, interval: float = 3.0, *, event_name: str = ""):
        """釋票監測 — `!watch [間隔秒數] [活動名稱]`"""
        if self._status != "idle":
            await self._send_error(ctx, "無法啟動", f"目前正在 **{self._status}**，請先 `!stop`")
            return

        cfg = self._load_cfg()
        ev = self._get_event(cfg, event_name.strip() or None)
        if not ev:
            await self._send_error(ctx, "找不到活動", "請確認 `config.yaml` 已設定 tixcraft 活動")
            return

        self._status = "watching"
        await self._send_embed(
            ctx, "開始監測釋票 👀",
            f"**活動：** {ev.name}\n**日期：** {ev.date_keyword or '第一個可用'}\n"
            f"**間隔：** {interval} 秒\n按 `!stop` 停止監測",
        )

        async def _do_watch():
            sess = cfg.sessions[0]
            if cfg.browser.api_mode != "off":
                from ticket_bot.platforms.tixcraft_api import TixcraftApiBot
                bot = TixcraftApiBot(cfg, ev, session=sess)
            else:
                from ticket_bot.platforms.tixcraft import TixcraftBot
                bot = TixcraftBot(cfg, ev, session=sess)
            self._active_bot = bot
            try:
                success = await bot.watch(interval=interval)
                if success:
                    await self._send_success(
                        ctx, "釋票搶到了！ 🎉",
                        f"**{ev.name}**\n請在瀏覽器中 **10 分鐘內完成付款**！",
                    )
                    await asyncio.sleep(600)
                else:
                    await self._send_error(ctx, "監測結束", "未搶到票")
            except asyncio.CancelledError:
                await self._send_embed(ctx, "監測已停止", "使用者手動停止")
            except Exception as e:
                logger.exception("監測錯誤")
                await self._send_error(ctx, "監測錯誤", f"```{e}```")
            finally:
                await bot.close()
                self._active_bot = None
                self._status = "idle"

        self._active_task = asyncio.create_task(_do_watch())

    # ── !stop ────────────────────────────────────────────────

    @commands.is_owner()
    @commands.command(name="stop", aliases=["x"])
    async def stop_ticket(self, ctx):
        """停止搶票或監測"""
        if self._status == "idle":
            await self._send_embed(ctx, "沒有執行中的任務", "目前處於待命狀態")
            return

        old_status = self._status
        if self._active_task and not self._active_task.done():
            self._active_task.cancel()

        if self._active_bot:
            try:
                await self._active_bot.close()
            except Exception:
                pass
            self._active_bot = None

        self._status = "idle"
        self._active_task = None
        await self._send_embed(ctx, "已停止 ⏹️", f"已停止 **{old_status}** 任務")

    # ── !list ────────────────────────────────────────────────

    @commands.is_owner()
    @commands.command(name="list", aliases=["l"])
    async def list_events(self, ctx):
        """列出 config.yaml 中的活動"""
        cfg = self._load_cfg()
        tix_events = [e for e in cfg.events if e.platform == "tixcraft"]

        if not tix_events:
            await self._send_error(ctx, "無活動", "config.yaml 中沒有 tixcraft 活動")
            return

        desc = ""
        for i, ev in enumerate(tix_events, 1):
            desc += f"**{i}. {ev.name}**\n"
            desc += f"   日期: {ev.date_keyword or '未指定'} / 區域: {ev.area_keyword or '未指定'} / 票數: {ev.ticket_count}\n\n"

        await self._send_embed(ctx, "活動列表", desc)

    # ── !config ──────────────────────────────────────────────

    @commands.is_owner()
    @commands.command(name="config", aliases=["cfg"])
    async def show_config(self, ctx, key: str = "", value: str = ""):
        """查看或修改搶票設定 — `!config [key] [value]`

        查看全部: `!config`
        修改設定: `!config date 2026/06/14`
        可修改: date, area, count
        """
        cfg = self._load_cfg()
        ev = self._get_event(cfg)

        if not ev:
            await self._send_error(ctx, "無活動", "config.yaml 中沒有 tixcraft 活動")
            return

        if not key:
            # 顯示目前設定
            desc = (
                f"**活動：** {ev.name}\n"
                f"**URL：** {ev.url}\n"
                f"**date：** `{ev.date_keyword or '(未指定)'}`\n"
                f"**area：** `{ev.area_keyword or '(未指定)'}`\n"
                f"**count：** `{ev.ticket_count}`\n"
                f"**引擎：** `{cfg.browser.engine}`\n"
                f"**Headless：** `{cfg.browser.headless}`\n\n"
                f"修改範例: `!config date 2026/06/14`"
            )
            await self._send_embed(ctx, "目前設定", desc)
            return

        # 動態修改（僅修改記憶體中的值，不寫入 config.yaml）
        key = key.lower()
        if key == "date":
            ev.date_keyword = value
            await self._send_success(ctx, "設定已更新", f"**date_keyword** → `{value}`")
        elif key == "area":
            ev.area_keyword = value
            await self._send_success(ctx, "設定已更新", f"**area_keyword** → `{value}`")
        elif key == "count":
            try:
                ev.ticket_count = int(value)
                await self._send_success(ctx, "設定已更新", f"**ticket_count** → `{value}`")
            except ValueError:
                await self._send_error(ctx, "無效數值", f"`{value}` 不是有效的票數")
        else:
            await self._send_error(ctx, "未知設定", f"`{key}` 不是可修改的設定\n可用: `date`, `area`, `count`")

    # ── !ping ────────────────────────────────────────────────

    @commands.is_owner()
    @commands.command(name="ping")
    async def ping(self, ctx):
        """測試 Bot 連線"""
        latency = round(self.bot.latency * 1000)
        await self._send_success(ctx, "Pong! 🏓", f"延遲: **{latency}ms**")

    # ── 錯誤處理 ─────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_command_error(self, ctx, error):
        if isinstance(error, commands.NotOwner):
            await self._send_error(ctx, "權限不足", "你沒有權限執行這個指令！只有 Bot 擁有者可以操作。")
        elif isinstance(error, commands.CommandNotFound):
            pass
        else:
            logger.error("Discord 指令錯誤: %s", error)

# ── Bot 啟動 ─────────────────────────────────────────────────

def create_bot(config_path: str = "config.yaml") -> commands.Bot:
    """建立 Discord Bot instance"""
    intents = discord.Intents.default()
    intents.message_content = True

    bot = commands.Bot(
        command_prefix=COMMAND_PREFIX,
        intents=intents,
        help_command=commands.DefaultHelpCommand(no_category="其他"),
    )

    @bot.event
    async def on_ready():
        logger.info("Discord Bot 已上線: %s (ID: %s)", bot.user.name, bot.user.id)
        await bot.change_presence(
            activity=discord.Game(name=BOT_STATUS),
        )

    # 註冊指令（透過 setup_hook 在 bot 內部 event loop 中 await）
    @bot.event
    async def setup_hook():
        await bot.add_cog(TicketBotCog(bot, config_path=config_path))

    return bot


def run_bot(config_path: str = "config.yaml"):
    """啟動 Discord Bot（blocking）"""
    load_dotenv()
    token = os.getenv("DISCORD_BOT_TOKEN", "")
    if not token:
        raise RuntimeError("DISCORD_BOT_TOKEN 未設定，請在 .env 中填入 Bot Token")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    bot = create_bot(config_path=config_path)
    bot.run(token, log_handler=None)
