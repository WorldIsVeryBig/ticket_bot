import click

from ticket_bot.cli import _plan_watch_targets, _watch_session_sequence
from ticket_bot.config import EventConfig, SessionConfig


def _event(name: str) -> EventConfig:
    return EventConfig(name=name, platform="tixcraft", url=f"https://example.com/{name}")


def _session(name: str, profile: str) -> SessionConfig:
    return SessionConfig(name=name, user_data_dir=profile)


def test_plan_watch_single_event_single_session():
    plan = _plan_watch_targets(
        [_event("IVE")],
        [_session("a", "./profile_a"), _session("b", "./profile_b")],
        parallel=False,
    )

    assert [(ev.name, [sess.name for sess in assigned]) for ev, assigned in plan] == [
        ("IVE", ["a"])
    ]


def test_watch_session_sequence_uses_all_sessions_for_single_event_failover():
    targets = [_event("IVE")]
    sessions = [_session("a", "./profile_a"), _session("b", "./profile_b")]
    plan = _plan_watch_targets(targets, sessions, parallel=False)

    sequence = _watch_session_sequence(targets, plan, sessions, parallel=False)

    assert [sess.name for sess in sequence] == ["a", "b"]


def test_plan_watch_multi_event_single_session_each():
    plan = _plan_watch_targets(
        [_event("IVE"), _event("ITZY")],
        [_session("a", "./profile_a"), _session("b", "./profile_b")],
        parallel=False,
    )

    assert [(ev.name, [sess.name for sess in assigned]) for ev, assigned in plan] == [
        ("IVE", ["a"]),
        ("ITZY", ["b"]),
    ]


def test_plan_watch_single_event_parallel_uses_all_sessions():
    plan = _plan_watch_targets(
        [_event("IVE")],
        [_session("a", "./profile_a"), _session("b", "./profile_b")],
        parallel=True,
    )

    assert [(ev.name, [sess.name for sess in assigned]) for ev, assigned in plan] == [
        ("IVE", ["a", "b"])
    ]


def test_plan_watch_multi_event_parallel_distributes_sessions_round_robin():
    plan = _plan_watch_targets(
        [_event("IVE"), _event("ITZY")],
        [
            _session("a", "./profile_a"),
            _session("b", "./profile_b"),
            _session("c", "./profile_c"),
            _session("d", "./profile_d"),
        ],
        parallel=True,
    )

    assert [(ev.name, [sess.name for sess in assigned]) for ev, assigned in plan] == [
        ("IVE", ["a", "c"]),
        ("ITZY", ["b", "d"]),
    ]


def test_plan_watch_multi_event_requires_enough_sessions():
    try:
        _plan_watch_targets(
            [_event("IVE"), _event("ITZY")],
            [_session("a", "./profile_a")],
            parallel=False,
        )
        assert False, "expected ClickException"
    except click.ClickException as exc:
        assert "至少需要與活動數相同的 sessions" in exc.message


def test_plan_watch_rejects_duplicate_profiles():
    try:
        _plan_watch_targets(
            [_event("IVE"), _event("ITZY")],
            [_session("a", "./shared"), _session("b", "./shared")],
            parallel=False,
        )
        assert False, "expected ClickException"
    except click.ClickException as exc:
        assert "不同的 user_data_dir" in exc.message
