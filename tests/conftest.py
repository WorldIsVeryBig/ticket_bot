"""pytest 共用 fixtures"""

import pytest


@pytest.fixture()
def httpx_mock(monkeypatch):
    """簡易 httpx mock — 攔截所有 httpx.AsyncClient 請求"""
    import httpx

    class _HttpxMock:
        def __init__(self):
            self._responses = []
            self._callbacks = []

        def add_response(self, *, url=None, url__regex=None, status_code=200, text="", json=None):
            self._responses.append({
                "url": url,
                "url_regex": url__regex,
                "status_code": status_code,
                "text": text,
                "json": json,
            })

        def add_callback(self, callback, *, url=None):
            self._callbacks.append({"url": url, "callback": callback})

        def _match(self, request):
            import re
            url_str = str(request.url)

            for cb in self._callbacks:
                if cb["url"] is None or cb["url"] in url_str:
                    return cb["callback"](request)

            for resp in self._responses:
                if resp["url"] and resp["url"] in url_str:
                    return httpx.Response(
                        status_code=resp["status_code"],
                        text=resp["text"],
                        json=resp["json"],
                    )
                if resp["url_regex"] and re.search(resp["url_regex"], url_str):
                    return httpx.Response(
                        status_code=resp["status_code"],
                        text=resp["text"],
                        json=resp["json"],
                    )

            return httpx.Response(status_code=200, text="")

    mock = _HttpxMock()

    class MockTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            resp = mock._match(request)
            return resp

    original_init = httpx.AsyncClient.__init__

    def patched_init(self_client, *args, **kwargs):
        kwargs.pop("cookies", None)
        kwargs["transport"] = MockTransport()
        kwargs["base_url"] = kwargs.get("base_url", "")
        original_init(self_client, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)

    return mock
