from ticket_bot.config import KKTIXAutofillConfig
from ticket_bot.platforms.kktix import build_order_autofill_plan


def test_build_order_autofill_plan_uses_contact_defaults():
    plan = build_order_autofill_plan(
        KKTIXAutofillConfig(
            enabled=True,
            contact_name="王小明",
            contact_email="demo@example.com",
            contact_phone="0912345678",
            contact_gender="male",
            contact_birth_date="1990-01-02",
            contact_region="taipei",
            attendee_id_numbers=["A123456789"],
        ),
        attendee_count=2,
    )

    assert plan["enabled"] is True
    assert plan["contact"] == {
        "name": "王小明",
        "email": "demo@example.com",
        "phone": "0912345678",
        "birth_date": "1990/01/02",
        "gender_candidates": ["male", "男", "m"],
        "region_candidates": ["taipei", "北北基宜地區", "北北基宜", "台北"],
    }
    assert plan["attendees"] == [
        {
            "name": "王小明",
            "phone": "0912345678",
            "id_number": "A123456789",
            "agree_real_name": True,
        },
        {
            "name": "王小明",
            "phone": "0912345678",
            "id_number": "A123456789",
            "agree_real_name": True,
        },
    ]


def test_build_order_autofill_plan_honors_per_attendee_values():
    plan = build_order_autofill_plan(
        KKTIXAutofillConfig(
            enabled=True,
            contact_name="聯絡人",
            contact_phone="0900000000",
            attendee_names=["張三", "李四"],
            attendee_phones=["0911111111", "0922222222"],
            attendee_id_numbers=["A123456789", "B223456789"],
            agree_real_name=False,
            display_public_attendance=True,
            join_organizer_fan=True,
        ),
        attendee_count=2,
    )

    assert plan["attendees"] == [
        {
            "name": "張三",
            "phone": "0911111111",
            "id_number": "A123456789",
            "agree_real_name": False,
        },
        {
            "name": "李四",
            "phone": "0922222222",
            "id_number": "B223456789",
            "agree_real_name": False,
        },
    ]
    assert plan["options"] == {
        "display_public_attendance": True,
        "join_organizer_fan": True,
    }


def test_build_order_autofill_plan_supports_new_taipei_region_alias():
    plan = build_order_autofill_plan(
        KKTIXAutofillConfig(
            enabled=True,
            contact_region="新北市",
        ),
        attendee_count=1,
    )

    assert plan["contact"]["region_candidates"] == [
        "新北市",
        "北北基宜地區",
        "北北基宜",
        "台北",
        "new taipei",
    ]
