import types

from core.agent import Agent


def trim(history, keep, monkeypatch):
    """Helper: run _trim_history on an arbitrary history list with a fixed history_keep."""
    obj = types.SimpleNamespace(history=history, history_keep=keep)
    return Agent._trim_history(obj)  # type: ignore[arg-type]


SYS = {"role": "system", "content": "sys"}


def msg(role: str, n: int) -> dict:
    return {"role": role, "content": str(n)}


def test_system_always_kept(monkeypatch):
    history = [SYS] + [msg("user", i) for i in range(20)]
    result = trim(history, keep=5, monkeypatch=monkeypatch)
    assert result[0] == SYS


def test_trims_to_last_keep_pairs(monkeypatch):
    # history_keep 以 pair 計算：keep=3 → 保留最後 6 則
    history = [SYS] + [msg("user", i) for i in range(10)]
    result = trim(history, keep=3, monkeypatch=monkeypatch)
    assert result == [SYS] + [msg("user", i) for i in range(4, 10)]


def test_short_history_kept_in_full(monkeypatch):
    history = [SYS, msg("user", 0), msg("assistant", 1)]
    result = trim(history, keep=10, monkeypatch=monkeypatch)
    assert result == history


def test_no_system_message(monkeypatch):
    history = [msg("user", i) for i in range(5)]
    result = trim(history, keep=1, monkeypatch=monkeypatch)
    assert result == [msg("user", 3), msg("user", 4)]


def test_system_not_counted_toward_keep(monkeypatch):
    # system message must not consume a slot in the HISTORY_KEEP budget
    history = [SYS] + [msg("user", i) for i in range(3)]
    result = trim(history, keep=3, monkeypatch=monkeypatch)
    assert len(result) == 4  # 1 system + 3 user
    assert result[0] == SYS


def test_empty_history(monkeypatch):
    assert trim([], keep=5, monkeypatch=monkeypatch) == []


def test_tool_messages_counted_as_non_system(monkeypatch):
    history = [SYS, msg("tool", 0), msg("tool", 1), msg("tool", 2), msg("tool", 3)]
    result = trim(history, keep=1, monkeypatch=monkeypatch)
    assert result == [SYS, msg("tool", 2), msg("tool", 3)]


def test_private_fields_stripped(monkeypatch):
    # _ 開頭的內部欄位不送給模型
    history = [SYS, {"role": "user", "content": "hi", "_sender": "pm"}]
    result = trim(history, keep=5, monkeypatch=monkeypatch)
    assert result == [SYS, {"role": "user", "content": "hi"}]
