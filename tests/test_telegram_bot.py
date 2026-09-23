"""telegram bot NLU 測試"""

from ticket_bot.telegram_bot import match_nlu_rules


def test_match_nlu_rules_prefers_info_over_list():
    assert match_nlu_rules("活動資訊") == "/info"
    assert match_nlu_rules("這活動幾點開賣") == "/info"


def test_match_nlu_rules_list_keywords_still_work():
    assert match_nlu_rules("列出活動") == "/list"
    assert match_nlu_rules("有什麼活動") == "/list"
