"""notifications 模組測試"""

import pytest
import httpx

from ticket_bot.notifications.telegram import send_telegram
from ticket_bot.notifications.discord import send_discord


@pytest.mark.asyncio
async def test_telegram_success(httpx_mock):
    """Telegram 發送成功"""
    httpx_mock.add_response(url__regex=r".*api\.telegram\.org.*", status_code=200)

    await send_telegram(
        bot_token="test-token",
        chat_id="123",
        event_name="測試活動",
        url="https://example.com",
        status="搶票成功",
    )
    # 不拋例外即為成功


@pytest.mark.asyncio
async def test_telegram_failure(httpx_mock):
    """Telegram 發送失敗（不拋例外，只 log）"""
    httpx_mock.add_response(url__regex=r".*api\.telegram\.org.*", status_code=400, text="Bad Request")

    await send_telegram(
        bot_token="bad-token",
        chat_id="123",
        event_name="測試",
        status="搶票失敗",
    )


@pytest.mark.asyncio
async def test_discord_success(httpx_mock):
    """Discord 發送成功"""
    httpx_mock.add_response(url="https://discord.com/api/webhooks/test", status_code=204)

    await send_discord(
        webhook_url="https://discord.com/api/webhooks/test",
        event_name="測試活動",
        url="https://example.com",
        status="搶票成功",
    )


@pytest.mark.asyncio
async def test_discord_embed_fields(httpx_mock):
    """Discord embed 包含正確欄位"""
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(204)

    httpx_mock.add_callback(handler, url="https://discord.com/api/webhooks/test")

    await send_discord(
        webhook_url="https://discord.com/api/webhooks/test",
        event_name="演唱會",
        status="搶票成功",
        platform="tixcraft",
    )

    assert len(requests) == 1
    import json
    body = json.loads(requests[0].content)
    assert body["username"] == "Ticket Bot"
    assert body["embeds"][0]["title"] == "演唱會"
    fields = {f["name"]: f["value"] for f in body["embeds"][0]["fields"]}
    assert fields["平台"] == "tixcraft"
    assert fields["狀態"] == "搶票成功"
