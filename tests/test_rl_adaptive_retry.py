"""Q-Learning Adaptive Retry 測試"""

import time

from ticket_bot.rl.adaptive_retry import (
    AdaptiveRetry,
    WAIT_ACTIONS,
    _retry_bucket,
    _elapsed_bucket,
    _classify_response,
)


def test_retry_bucket():
    assert _retry_bucket(0) == "first"
    assert _retry_bucket(1) == "second"
    assert _retry_bucket(2) == "third"
    assert _retry_bucket(5) == "many"


def test_elapsed_bucket():
    assert _elapsed_bucket(2) == "burst"
    assert _elapsed_bucket(10) == "early"
    assert _elapsed_bucket(30) == "mid"
    assert _elapsed_bucket(120) == "late"


def test_classify_response():
    assert _classify_response(200, None) == "success"
    assert _classify_response(302, None) == "success"
    assert _classify_response(429, None) == "rate_limit"
    assert _classify_response(503, None) == "server_error"
    assert _classify_response(None, TimeoutError()) == "timeout"
    assert _classify_response(None, ValueError()) == "other_error"


def test_get_wait_time_returns_valid_action(tmp_path):
    """get_wait_time() 回傳的值必須在動作空間中"""
    ar = AdaptiveRetry(persist_path=tmp_path / "q.json")
    ar.start_episode()
    for _ in range(5):
        wt = ar.get_wait_time(status_code=503)
        assert wt in WAIT_ACTIONS


def test_should_retry_respects_max(tmp_path):
    """重試次數超過上限後 should_retry 為 False"""
    ar = AdaptiveRetry(max_retries=3, persist_path=tmp_path / "q.json")
    ar.start_episode()
    ar.get_wait_time(status_code=503)
    ar.get_wait_time(status_code=503)
    ar.get_wait_time(status_code=503)
    assert not ar.should_retry


def test_update_changes_qtable(tmp_path):
    """update() 後 Q-table 值應改變"""
    ar = AdaptiveRetry(alpha=0.5, persist_path=tmp_path / "q.json")
    ar.start_episode()
    ar.get_wait_time(status_code=503)

    state = ar._last_state
    action_str = str(ar._last_action)
    old_q = ar._q[state][action_str]

    ar.update(success=True, status_code=200)
    new_q = ar._q[state][action_str]
    assert new_q != old_q  # 成功後 Q 值應該改變


def test_persistence(tmp_path):
    """持久化後重新載入 Q-table"""
    path = tmp_path / "q.json"
    ar1 = AdaptiveRetry(persist_path=path)
    ar1.start_episode()
    ar1.get_wait_time(status_code=503)
    ar1.update(success=True, status_code=200)

    ar2 = AdaptiveRetry(persist_path=path)
    # 至少有一個 Q 值不是 0
    has_nonzero = any(
        v != 0.0 for actions in ar2._q.values() for v in actions.values()
    )
    assert has_nonzero


def test_epsilon_decays(tmp_path):
    """每次 update 後 epsilon 應該衰減"""
    ar = AdaptiveRetry(epsilon=0.5, epsilon_decay=0.9, persist_path=tmp_path / "q.json")
    ar.start_episode()
    ar.get_wait_time(status_code=503)

    old_eps = ar.epsilon
    ar.update(success=False, status_code=503)
    assert ar.epsilon < old_eps


def test_epsilon_min_floor(tmp_path):
    """epsilon 不應低於 epsilon_min"""
    ar = AdaptiveRetry(
        epsilon=0.06, epsilon_decay=0.5, epsilon_min=0.05,
        persist_path=tmp_path / "q.json",
    )
    ar.start_episode()
    ar.get_wait_time(status_code=503)
    ar.update(success=False, status_code=503)
    assert ar.epsilon >= ar.epsilon_min


def test_stats_shows_learned(tmp_path):
    """stats() 應該只顯示有學過的 state"""
    ar = AdaptiveRetry(alpha=1.0, persist_path=tmp_path / "q.json")
    ar.start_episode()
    ar.get_wait_time(status_code=503)
    ar.update(success=True, status_code=200)

    s = ar.stats()
    assert len(s) > 0
    for state, info in s.items():
        assert "best_action" in info
        assert "q_value" in info
