import asyncio
import os
import time
import nodriver as uc

async def main():
    browser = await uc.start(
        headless=True,
        user_data_dir="./chrome_profile",
        browser_executable_path=os.getenv("BROWSER_EXECUTABLE_PATH", ""),
    )
    page = await browser.get("https://tixcraft.com/user/order")
    await page.sleep(5)
    await page.save_screenshot("debug_orders.png")
    
    # Check if there's any active order
    html = await page.evaluate("document.body.innerHTML")
    if "等待付款" in html or "訂單成立" in html:
        print("FOUND ACTIVE ORDERS")
    else:
        print("NO ORDERS FOUND")
        
    browser.stop()

if __name__ == "__main__":
    asyncio.run(main())
