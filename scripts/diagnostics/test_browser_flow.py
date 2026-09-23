import asyncio
import logging
import sys
import time
from pathlib import Path

# 加入 src 路徑
sys.path.append(str(Path(__file__).resolve().parents[2] / "src"))

from ticket_bot.config import load_config
from ticket_bot.platforms.tixcraft_api import TixcraftApiBot

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("BrowserTest")

async def test_browser_workflow(rounds=1):
    # 1. 載入設定
    cfg = load_config("config.yaml")
    
    # 2. 修改活動資訊，鎖定目標網址
    target_url = "https://tixcraft.com/ticket/area/26_della/21450"
    event = cfg.events[0]
    event.url = target_url
    event.name = "Della 實戰測試 (Hybrid API)"
    event.ticket_count = 2
    
    # 3. 開啟 API 模式
    cfg.browser.api_mode = "full"
    cfg.browser.headless = False # 讓你親眼看見
    cfg.browser.turbo_mode = True # 極速模式測試
    
    # 4. 初始化 TixcraftApiBot
    bot = TixcraftApiBot(cfg, event, session=cfg.sessions[0])
    
    try:
        logger.info("🚀 開始『混合 API (Hybrid)』正式流程實戰測試...")
        
        start_time = time.time()
        
        # 直接調用 bot.run() 模擬正式上線的行為
        # bot.run() 會自動完成: 登入檢查 -> 進入 game 頁面 -> 取得 area -> 進入 ticket -> 填寫送出 -> 結帳
        success = await bot.run()
        
        if success:
            logger.info("✅ 訂單流程完成！正在截圖保存至 success_order.png")
            try:
                # 給予一點時間讓最終結帳畫面載入
                await asyncio.sleep(2)
                final_img = await bot.page.screenshot()
                with open("success_order.png", "wb") as f:
                    f.write(final_img)
            except Exception:
                pass
        else:
            logger.warning("❌ 搶票流程未能順利完成")
            
        elapsed = time.time() - start_time
        logger.info(f"⏱️ 總耗時: {elapsed:.2f} 秒")
        
    except Exception as e:
        logger.exception(f"發生意外錯誤: {e}")
    finally:
        # 直接關閉 bot
        if bot:
            await bot.close()

if __name__ == "__main__":
    asyncio.run(test_browser_workflow(rounds=1))
