"""Gemma 4 RL 元策略顧問 — 用 LLM 推理分析和改善 RL 策略

不替代現有的 Bandit/Q-Learning 即時決策（那些需要 ms 級延遲），
而是在搶票前後提供策略分析、超參數建議和表現報告。
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ticket_bot.gemma_client import GemmaClient

logger = logging.getLogger(__name__)


# ── Prompt Templates ─────────────────────────────────────────

PRE_SESSION_PROMPT = """\
你是搶票系統的 AI 策略顧問。根據以下資訊，分析並建議最佳搶票策略。

## 活動資訊
- 名稱：{event_name}
- 平台：{platform}
- 日期：{date_keyword}
- 目標票數：{ticket_count}
- 區域偏好：{area_keyword}

## 歷史 RL 統計

### Captcha Bandit（各 threshold 的成功率）
{captcha_stats}

### Burst Bandit（各延遲下的 burst 模式表現）
{burst_stats}

### Retry Q-table（各情境的最佳重試策略）
{retry_stats}

## 請用 JSON 格式回答：
{{
    "captcha_threshold": 建議的 confidence threshold (0.1-0.9),
    "burst_pattern": "建議的 burst 模式名稱",
    "retry_aggressiveness": "aggressive | moderate | conservative",
    "epsilon": 建議的探索率 (0.01-0.3),
    "reasoning": "分析推理過程（用繁體中文，3-5句）",
    "risk_level": "high | medium | low（預估搶票難度）",
    "tips": ["具體的操作建議1", "建議2"]
}}
"""

POST_SESSION_PROMPT = """\
你是搶票系統的 AI 分析師。分析以下搶票結果，提供改善建議。

## 搶票結果
- 活動：{event_name}
- 結果：{result}
- 總耗時：{elapsed_seconds:.1f} 秒
- 驗證碼嘗試次數：{captcha_attempts}
- 驗證碼成功率：{captcha_success_rate}
- 重試次數：{retry_count}

## RL 系統當前狀態

### Captcha Bandit
{captcha_stats}

### Burst Bandit
{burst_stats}

### Retry Q-table（學習過的 state-action）
{retry_stats}

## 請用繁體中文提供：
1. 本次搶票表現評分（1-10）
2. 各系統的表現分析（驗證碼、重試、burst 時間）
3. 具體的改善建議（至少 3 點）
4. 下次搶票前應該調整的設定
"""

REWARD_SHAPING_PROMPT = """\
你是強化學習系統的 reward engineering 專家。分析以下 Q-table 和最近的 episodes，
建議是否需要調整 reward function。

## 目前 Reward 設計
- 成功搶票：+1.0 - 0.1 × 等待秒數
- 失敗：-0.1 × 等待秒數

## Q-table 統計（只列出有學習過的 state-action）
{q_stats}

## 最近 5 次搶票的 episode 摘要
{recent_episodes}

## 請用 JSON 格式建議 reward 調整：
{{
    "current_assessment": "目前 reward function 的評估",
    "suggested_changes": [
        {{
            "scenario": "描述情境",
            "current_reward": "目前的 reward 計算",
            "suggested_reward": "建議的 reward 計算",
            "reason": "原因"
        }}
    ],
    "alpha_suggestion": 建議的學習率,
    "gamma_suggestion": 建議的折扣因子,
    "summary": "總結（繁體中文）"
}}
"""


class GemmaRLAdvisor:
    """用 Gemma 4 的推理能力分析和改善 RL 策略

    角色定位：「元策略顧問」
    - 不做即時決策（延遲太高）
    - 搶票前分析歷史數據，建議策略
    - 搶票後分析表現，建議改善
    - 定期分析 reward function 是否需要調整
    """

    def __init__(self, gemma: GemmaClient):
        self.gemma = gemma

    async def pre_session_advice(
        self,
        event_info: dict,
        captcha_stats: dict,
        burst_stats: dict,
        retry_stats: dict,
    ) -> dict | None:
        """搶票前策略分析

        Args:
            event_info: 活動資訊 (name, platform, date_keyword, ticket_count, area_keyword)
            captcha_stats: ThresholdBandit.stats() 的結果
            burst_stats: BurstBandit.stats() 的結果
            retry_stats: AdaptiveRetry.stats() 的結果

        Returns:
            策略建議 dict，或 None（推理失敗）
        """
        prompt = PRE_SESSION_PROMPT.format(
            event_name=event_info.get("name", "未知"),
            platform=event_info.get("platform", "tixcraft"),
            date_keyword=event_info.get("date_keyword", "未指定"),
            ticket_count=event_info.get("ticket_count", 1),
            area_keyword=event_info.get("area_keyword", "不限"),
            captcha_stats=json.dumps(captcha_stats, indent=2, ensure_ascii=False),
            burst_stats=_format_burst_stats(burst_stats),
            retry_stats=json.dumps(retry_stats, indent=2, ensure_ascii=False),
        )

        result = await self.gemma.structured_chat(
            prompt,
            system="你是搶票策略專家。用 JSON 格式回答，內含 reasoning 用繁體中文。",
            temperature=0.2,
        )

        if result:
            logger.info("Gemma RL Advisor 搶票前建議: %s", result.get("reasoning", "")[:100])
        return result

    async def post_session_analysis(
        self,
        event_name: str,
        success: bool,
        elapsed_seconds: float,
        captcha_attempts: int,
        captcha_success_rate: float,
        retry_count: int,
        captcha_stats: dict,
        burst_stats: dict,
        retry_stats: dict,
    ) -> str:
        """搶票後表現分析

        Returns:
            繁體中文分析報告
        """
        prompt = POST_SESSION_PROMPT.format(
            event_name=event_name,
            result="✅ 成功搶到" if success else "❌ 未搶到",
            elapsed_seconds=elapsed_seconds,
            captcha_attempts=captcha_attempts,
            captcha_success_rate=f"{captcha_success_rate:.0%}" if captcha_success_rate else "N/A",
            retry_count=retry_count,
            captcha_stats=json.dumps(captcha_stats, indent=2, ensure_ascii=False),
            burst_stats=_format_burst_stats(burst_stats),
            retry_stats=json.dumps(retry_stats, indent=2, ensure_ascii=False),
        )

        result = await self.gemma.chat(
            prompt,
            system="你是搶票系統分析師。用繁體中文提供簡潔、有 actionable insights 的分析。",
            temperature=0.3,
            max_tokens=800,
        )

        return result or "分析失敗，請稍後再試。"

    async def suggest_reward_shaping(
        self,
        q_stats: dict,
        recent_episodes: list[dict],
    ) -> dict | None:
        """分析 Q-table 並建議 reward function 調整

        Args:
            q_stats: AdaptiveRetry.stats() 結果
            recent_episodes: 最近幾次搶票的摘要 [{result, elapsed, retries, ...}]

        Returns:
            Reward 調整建議 dict
        """
        prompt = REWARD_SHAPING_PROMPT.format(
            q_stats=json.dumps(q_stats, indent=2, ensure_ascii=False),
            recent_episodes=json.dumps(recent_episodes, indent=2, ensure_ascii=False),
        )

        return await self.gemma.structured_chat(
            prompt,
            system="你是 RL reward engineering 專家。用 JSON 格式回答。",
            temperature=0.2,
        )

    async def explain_rl_stats(
        self,
        captcha_stats: dict,
        burst_stats: dict,
        retry_stats: dict,
    ) -> str:
        """用自然語言解讀 RL 統計數據（給使用者看）

        Returns:
            繁體中文解讀
        """
        prompt = f"""幫我解讀以下搶票機器人的 RL 學習統計，用繁體中文、面向非技術使用者的方式說明。

## Captcha Bandit（驗證碼信心閾值選擇器）
每個 arm 代表一個閾值，mean 是歷史成功率，trials 是嘗試次數。
{json.dumps(captcha_stats, indent=2)}

## Burst Bandit（開賣時間爆發模式選擇器）
根據網路延遲（bucket）選擇最佳的發送時機模式（pattern）。
{_format_burst_stats(burst_stats)}

## Retry Q-table（重試等待時間選擇器）
根據情境（重試次數×距開賣時間×回應類型）選擇最佳等待時間。
{json.dumps(retry_stats, indent=2)}

請提供：
1. 一段總結（3-4 句話）
2. 每個系統的關鍵發現
3. 是否有需要注意的問題
"""

        return await self.gemma.chat(
            prompt,
            system="你是數據分析師，用簡單易懂的方式解釋 RL 系統的學習狀況。",
            temperature=0.3,
            max_tokens=600,
        )


def _format_burst_stats(stats: dict) -> str:
    """格式化 burst stats，只顯示有學習過的 bucket"""
    if not stats:
        return "尚無數據"
    lines = []
    for bucket, patterns in stats.items():
        active = {k: v for k, v in patterns.items() if v.get("trials", 0) > 0}
        if active:
            lines.append(f"  {bucket}: {json.dumps(active, ensure_ascii=False)}")
    return "\n".join(lines) if lines else "尚無數據"
