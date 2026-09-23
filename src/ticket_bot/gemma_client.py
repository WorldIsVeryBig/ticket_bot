"""Gemma 4 統一推理客戶端 — Ollama 本地推理

透過 Ollama REST API 呼叫本地 Gemma 4 模型，
支援純文字推理和多模態（文字+圖片）推理。
"""

from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

# 預設 system prompt
DEFAULT_SYSTEM = "你是搶票機器人的 AI 助手，使用繁體中文回答。回答簡潔扼要。"


@dataclass
class GemmaConfig:
    """Gemma 4 設定"""

    enabled: bool = False
    backend: str = "ollama"  # "ollama" | "api"（目前只實作 ollama）
    model: str = "gemma4:e4b"  # Ollama 模型名
    api_key: str = ""  # Google AI Studio（預留）
    ollama_url: str = "http://localhost:11434"
    timeout: float = 30.0  # 推理超時（秒）
    rl_advisor: bool = True  # 啟用 RL 元策略顧問


class GemmaClient:
    """Gemma 4 Ollama 推理客戶端

    透過 httpx 呼叫 Ollama REST API，不引入額外依賴。
    支援重試、超時、串流回應。
    """

    def __init__(self, config: GemmaConfig):
        self.config = config
        self._base_url = config.ollama_url.rstrip("/")
        self._model = config.model
        self._timeout = config.timeout
        self._available: bool | None = None  # 快取可用性檢查結果

    async def is_available(self) -> bool:
        """檢查 Ollama 服務和模型是否可用"""
        if self._available is not None:
            return self._available
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{self._base_url}/api/tags")
                if resp.status_code != 200:
                    self._available = False
                    return False
                data = resp.json()
                models = [m.get("name", "") for m in data.get("models", [])]
                # 檢查模型是否存在（允許部分匹配，例如 "gemma4:e4b" 匹配 "gemma4:e4b-q4_0"）
                model_base = self._model.split(":")[0]
                self._available = any(model_base in m for m in models)
                if not self._available:
                    logger.warning(
                        "Gemma 模型 '%s' 未安裝。已安裝: %s。請執行: ollama pull %s",
                        self._model,
                        models,
                        self._model,
                    )
                return self._available
        except Exception as e:
            logger.warning("Ollama 服務不可用: %s", e)
            self._available = False
            return False

    def reset_availability(self):
        """重設可用性快取（例如使用者手動啟動 Ollama 後）"""
        self._available = None

    async def chat(
        self,
        prompt: str,
        system: str = "",
        temperature: float = 0.3,
        max_tokens: int = 500,
    ) -> str:
        """純文字推理

        Args:
            prompt: 使用者訊息
            system: 系統提示詞（預設使用 DEFAULT_SYSTEM）
            temperature: 生成溫度（NLU 任務建議 0.1-0.3）
            max_tokens: 最大生成 token 數

        Returns:
            模型回應文字
        """
        if not await self.is_available():
            return ""

        system = system or DEFAULT_SYSTEM

        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(
                    f"{self._base_url}/api/chat",
                    json=payload,
                )
                if resp.status_code != 200:
                    logger.warning("Ollama API 錯誤: %d %s", resp.status_code, resp.text[:200])
                    return ""
                data = resp.json()
                content = data.get("message", {}).get("content", "")
                # 記錄推理時間
                total_ns = data.get("total_duration", 0)
                if total_ns:
                    logger.debug(
                        "Gemma 推理: %.1fs (prompt_eval=%.1fs, eval=%.1fs)",
                        total_ns / 1e9,
                        data.get("prompt_eval_duration", 0) / 1e9,
                        data.get("eval_duration", 0) / 1e9,
                    )
                return content.strip()
        except httpx.TimeoutException:
            logger.warning("Gemma 推理超時 (%.0fs)", self._timeout)
            return ""
        except Exception as e:
            logger.warning("Gemma 推理失敗: %s", e)
            return ""

    async def vision(
        self,
        prompt: str,
        image_bytes: bytes,
        system: str = "",
        temperature: float = 0.1,
        max_tokens: int = 200,
    ) -> str:
        """多模態推理（文字 + 圖片）

        Args:
            prompt: 使用者訊息（描述要對圖片做什麼）
            image_bytes: 圖片的 bytes 資料
            system: 系統提示詞
            temperature: 生成溫度
            max_tokens: 最大生成 token 數

        Returns:
            模型回應文字
        """
        if not await self.is_available():
            return ""

        system = system or DEFAULT_SYSTEM
        b64_image = base64.b64encode(image_bytes).decode("utf-8")

        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": prompt,
                    "images": [b64_image],
                },
            ],
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(
                    f"{self._base_url}/api/chat",
                    json=payload,
                )
                if resp.status_code != 200:
                    logger.warning("Ollama Vision API 錯誤: %d", resp.status_code)
                    return ""
                data = resp.json()
                return data.get("message", {}).get("content", "").strip()
        except httpx.TimeoutException:
            logger.warning("Gemma Vision 推理超時 (%.0fs)", self._timeout)
            return ""
        except Exception as e:
            logger.warning("Gemma Vision 推理失敗: %s", e)
            return ""

    async def structured_chat(
        self,
        prompt: str,
        system: str = "",
        temperature: float = 0.1,
    ) -> dict | None:
        """結構化推理 — 要求模型回傳 JSON

        Returns:
            解析後的 dict，失敗回傳 None
        """
        enhanced_prompt = (
            prompt + "\n\n請嚴格以 JSON 格式回答，不要包含 markdown 程式碼區塊標記。"
        )
        raw = await self.chat(
            enhanced_prompt,
            system=system,
            temperature=temperature,
        )
        if not raw:
            return None
        # 嘗試從回應中提取 JSON
        try:
            # 移除 markdown code block 標記
            cleaned = raw.strip()
            if cleaned.startswith("```"):
                # 移除 ```json 和 ```
                lines = cleaned.split("\n")
                lines = [l for l in lines if not l.strip().startswith("```")]
                cleaned = "\n".join(lines)
            return json.loads(cleaned)
        except json.JSONDecodeError:
            # 嘗試找到 JSON 子字串
            start = raw.find("{")
            end = raw.rfind("}") + 1
            if start >= 0 and end > start:
                try:
                    return json.loads(raw[start:end])
                except json.JSONDecodeError:
                    pass
            logger.warning("Gemma 回應無法解析為 JSON: %s", raw[:200])
            return None
