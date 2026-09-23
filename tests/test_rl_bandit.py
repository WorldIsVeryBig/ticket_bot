"""Thompson Sampling Bandit 測試"""

import json
from pathlib import Path

from ticket_bot.rl.bandit import ThresholdBandit


def test_select_returns_valid_arm(tmp_path):
    """select() 回傳的值必須在 arms 列表中"""
    bandit = ThresholdBandit(persist_path=tmp_path / "bandit.json")
    for _ in range(20):
        arm = bandit.select()
        assert arm in bandit.arms


def test_update_changes_params(tmp_path):
    """update() 後 alpha 或 beta 應該增加"""
    bandit = ThresholdBandit(persist_path=tmp_path / "bandit.json")
    bandit.select()
    arm = bandit._selected_arm

    old_alpha = bandit.alpha[arm]
    old_beta = bandit.beta[arm]

    bandit.update(success=True)
    assert bandit.alpha[arm] == old_alpha + 1
    assert bandit.beta[arm] == old_beta

    bandit.update(success=False)
    assert bandit.beta[arm] == old_beta + 1


def test_persistence(tmp_path):
    """持久化：儲存後重新載入應保留狀態"""
    path = tmp_path / "bandit.json"
    bandit1 = ThresholdBandit(persist_path=path)
    bandit1.update(threshold=0.5, success=True)
    bandit1.update(threshold=0.5, success=True)
    bandit1.update(threshold=0.5, success=True)

    # 重新載入
    bandit2 = ThresholdBandit(persist_path=path)
    assert bandit2.alpha[0.5] == 4.0  # 1 (prior) + 3 successes
    assert bandit2.beta[0.5] == 1.0


def test_stats(tmp_path):
    """stats() 回傳正確的統計"""
    bandit = ThresholdBandit(persist_path=tmp_path / "bandit.json")
    bandit.update(threshold=0.7, success=True)
    bandit.update(threshold=0.7, success=False)

    s = bandit.stats()
    assert 0.7 in s
    assert s[0.7]["trials"] == 2
    assert s[0.7]["alpha"] == 2.0
    assert s[0.7]["beta"] == 2.0


def test_convergence_towards_best_arm(tmp_path):
    """大量更新後，select() 應偏好成功率高的 arm"""
    bandit = ThresholdBandit(
        arms=[0.3, 0.6, 0.9],
        persist_path=tmp_path / "bandit.json",
    )
    # 讓 0.6 有很高的成功率
    for _ in range(50):
        bandit.update(threshold=0.6, success=True)
    for _ in range(50):
        bandit.update(threshold=0.3, success=False)
    for _ in range(50):
        bandit.update(threshold=0.9, success=False)

    # 抽樣 100 次，0.6 應該被選最多
    counts = {a: 0 for a in bandit.arms}
    for _ in range(100):
        counts[bandit.select()] += 1
    assert counts[0.6] > counts[0.3]
    assert counts[0.6] > counts[0.9]
