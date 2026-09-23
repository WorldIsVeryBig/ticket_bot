import asyncio
import logging
import sys
from pathlib import Path

# 加入 src 路徑
sys.path.append(str(Path(__file__).resolve().parents[2] / "src"))

from ticket_bot.config import load_config
from ticket_bot.platforms.tixcraft_api import TixcraftApiBot

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("LiveTest")

async def test_specific_url():
    # 1. 載入現有設定
    cfg = load_config("config.yaml")
    
    # 2. 修改活動資訊，直接鎖定目標網址
    target_url = "https://tixcraft.com/ticket/ticket/26_softbankh/21708/7/68"
    event = cfg.events[0]
    event.url = target_url
    event.name = "軟銀鷹實戰測試"
    event.ticket_count = 1 # 測試用，買一張就好
    
    # 3. 強制開啟 API 模式以達最快速度
    cfg.browser.api_mode = "full"
    cfg.browser.headless = False # 開啟瀏覽器讓你看過程，但邏輯走 API
    
    # 4. 初始化 Bot
    # 使用第一個 session (預設 chrome_profile)
    bot = TixcraftApiBot(cfg, event, session=cfg.sessions[0])
    
    try:
        logger.info("🚀 開始實戰流程測試...")
        await bot.start_browser()
        
        # 建立新分頁
        bot.page = await bot.engine.new_page("https://tixcraft.com/user/login")
        await asyncio.sleep(2)
        
        curr_url = await bot.page.current_url()
        if "login" in curr_url:
            logger.warning("⚠️ 偵測到未登入！請在瀏覽器完成登入，程式會等待 60 秒...")
            for i in range(60, 0, -10):
                logger.info(f"等待登入中... 剩餘 {i} 秒")
                await asyncio.sleep(10)
                if "login" not in await bot.page.current_url():
                    break
        
        # 重新初始化 API Session (取得最新的 Login Cookie)
        await bot._init_http()
        
        logger.info(f"🎯 直接衝向目標選位頁: {target_url}")
        # 在 API 模式下，我們直接調用 _fill_ticket_form_api
        success = await bot._fill_ticket_form_api(target_url)
        
        if success:
            logger.info("🔥 測試成功！已經成功送出訂單，請查看瀏覽器是否進入付款頁面。")
            logger.info("只要不點擊付款，就不會產生費用。")
            # 保持瀏覽器開啟 30 秒供確認
            await asyncio.sleep(30)
        else:
            logger.error("❌ 測試失敗，請檢查日誌輸出。")
            
    except Exception as e:
        logger.exception(f"發生意外錯誤: {e}")
    finally:
        await bot.close()

if __name__ == "__main__":
    asyncio.run(test_specific_url())
