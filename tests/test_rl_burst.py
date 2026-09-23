"""Burst Pattern Contextual Bandit 測試"""

from ticket_bot.rl.burst_bandit import BurstBandit, BURST_PATTERNS, _latency_to_bucket


def test_latency_buckets():
    """延遲值正確對應 bucket"""
    assert _latency_to_bucket(5) == "ultra_low"
    assert _latency_to_bucket(30) == "low"
    assert _latency_to_bucket(75) == "medium"
    assert _latency_to_bucket(150) == "high"
    assert _latency_to_bucket(500) == "very_high"


def test_select_returns_valid_pattern(tmp_path):
    """select() 回傳的 pattern 必須在預定義清單中"""
    bandit = BurstBandit(persist_path=tmp_path / "burst.json")
    for latency in [10, 30, 75, 150, 500]:
        name, offsets = bandit.select(latency)
        assert name in BURST_PATTERNS
        assert offsets == BURST_PATTERNS[name]


def test_update_changes_params(tmp_path):
    """update() 後 alpha/beta 應該改變"""
    bandit = BurstBandit(persist_path=tmp_path / "burst.json")
    bandit.select(30.0)  # low bucket
    bucket, pattern = bandit._last_selection

    old_alpha = bandit._params[bucket][pattern]["alpha"]
    bandit.update(success=True)
    assert bandit._params[bucket][pattern]["alpha"] == old_alpha + 1


def test_persistence(tmp_path):
    """持久化後重新載入"""
    path = tmp_path / "burst.json"
    b1 = BurstBandit(persist_path=path)
    b1.select(30.0)
    b1.update(success=True)
    b1.update(success=True)

    b2 = BurstBandit(persist_path=path)
    bucket, pattern = b1._last_selection
    assert b2._params[bucket][pattern]["alpha"] == 3.0  # 1 prior + 2


def test_different_contexts_independent(tmp_path):
    """不同 latency bucket 的 arm 應該獨立"""
    bandit = BurstBandit(persist_path=tmp_path / "burst.json")

    # 更新 low bucket
    bandit.update(success=True, bucket="low", pattern_name="standard")

    # ultra_low bucket 的 standard 不受影響
    assert bandit._params["ultra_low"]["standard"]["alpha"] == 1.0
    assert bandit._params["low"]["standard"]["alpha"] == 2.0


def test_stats(tmp_path):
    """stats() 回傳正確結構"""
    bandit = BurstBandit(persist_path=tmp_path / "burst.json")
    s = bandit.stats()
    assert "low" in s
    assert "standard" in s["low"]
    assert "mean" in s["low"]["standard"]
