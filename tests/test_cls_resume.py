"""
/cls 與 /resume 的 session 封存/還原邏輯測試。

所有測試都使用 tmp_path + 絕對路徑的 history_file，
不依賴 cwd，不需要 TUI。
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

from core.agent import Agent


# ── helpers：鏡像 session.py 的檔案邏輯，去掉 UI 呼叫 ────────────────────────

def agent_with_history(history_file: Path, messages: list[dict]) -> Agent:
    """建立一個有指定對話記錄的 Agent，並存到磁碟。"""
    a = Agent(model="gemma3:4b", history_file=history_file)
    for m in messages:
        a.history.append(m)
    a.save_history()
    return a


def do_cls(agent: Agent, resumed_from: Path | None = None) -> Path | None:
    """
    鏡像 action_clear 的檔案操作（不含 UI）。
    回傳新建的 archive 路徑，或 None（resumed_from 已設時直接刪除）。
    """
    hist_file = agent._history_file.resolve()
    hist_dir = hist_file.parent / ".history"

    real_msgs = [m for m in agent.history if m.get("role") != "system"]
    assert real_msgs, "do_cls 不應在空 history 上呼叫"

    if resumed_from is None:
        # 正常 /cls：把 history.json 移入 .history/，與 action_clear 邏輯同步
        hist_dir.mkdir(exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        archive = hist_dir / f"history_{ts}.json"
        n = 1
        while archive.exists():  # 同秒多次操作時避免覆蓋
            archive = hist_dir / f"history_{ts}_{n}.json"
            n += 1
        hist_file.rename(archive)
        agent.history = []
        agent._rebuild_system_prompt()
        agent.save_history()
        return archive
    else:
        # /cls 在 resume 後且未新增訊息：直接刪除，不重複歸檔
        hist_file.unlink(missing_ok=True)
        agent.history = []
        agent._rebuild_system_prompt()
        agent.save_history()
        return None


def do_resume_load(agent: Agent, archive: Path, resumed_from: Path | None = None) -> None:
    """
    鏡像 _resume_load 的完整檔案操作（不含 UI）。
    resumed_from 模擬 self._resumed_from 狀態：
      None → 目前有獨立對話（或剛送出訊息），切換前應自動歸檔
      Path → 剛 resume 尚未新增訊息，直接切換
    """
    real_msgs = [m for m in agent.history if m.get("role") != "system"]
    hist_file = agent._history_file.resolve()
    hist_dir = hist_file.parent / ".history"
    if real_msgs and resumed_from is None and hist_file.exists():
        hist_dir.mkdir(exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        auto_arch = hist_dir / f"history_{ts}.json"
        n = 1
        while auto_arch.exists():
            auto_arch = hist_dir / f"history_{ts}_{n}.json"
            n += 1
        hist_file.rename(auto_arch)
    elif resumed_from is not None and hist_file.exists():
        hist_file.unlink(missing_ok=True)
    shutil.copy2(archive, agent._history_file)
    agent.reset_history()


def list_archives(agent: Agent) -> list[Path]:
    """回傳 .history/ 下的歸檔清單（newest first）。"""
    hist_dir = agent._history_file.resolve().parent / ".history"
    if not hist_dir.exists():
        return []
    return sorted(hist_dir.glob("history_*.json"), reverse=True)


def user_contents(agent: Agent) -> list[str]:
    """取出 agent.history 中所有 user 訊息的文字內容。"""
    return [
        m["content"] if isinstance(m["content"], str)
        else next((b["text"] for b in m["content"] if b.get("type") == "text"), "")
        for m in agent.history
        if m.get("role") == "user"
    ]


# ── 基本存取測試 ──────────────────────────────────────────────────────────────

def test_agent_save_and_reload(tmp_path: Path) -> None:
    """save_history → 新 Agent 載入 → 訊息完整。"""
    hf = tmp_path / "history.json"
    agent_with_history(hf, [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ])
    a2 = Agent(model="gemma3:4b", history_file=hf)
    assert user_contents(a2) == ["hello"]


# ── /cls 封存測試 ─────────────────────────────────────────────────────────────

def test_cls_creates_archive(tmp_path: Path) -> None:
    """/cls 應把 history.json 移入 .history/。"""
    hf = tmp_path / "history.json"
    a = agent_with_history(hf, [{"role": "user", "content": "A"}])

    archive = do_cls(a)

    assert archive is not None
    assert archive.parent.name == ".history"
    assert archive.parent.parent == tmp_path
    # 原對話已移走；重建的 history.json 是空對話
    a2 = Agent(model="gemma3:4b", history_file=hf)
    assert user_contents(a2) == []


def test_cls_archive_contains_original_messages(tmp_path: Path) -> None:
    """封存檔案應包含原始訊息。"""
    hf = tmp_path / "history.json"
    a = agent_with_history(hf, [
        {"role": "user", "content": "original"},
        {"role": "assistant", "content": "reply"},
    ])
    archive = do_cls(a)
    assert archive is not None

    data = json.loads(archive.read_text())
    user_msgs = [m for m in data if m.get("role") == "user"]
    assert any(m["content"] == "original" for m in user_msgs)


def test_cls_archive_filename_format(tmp_path: Path) -> None:
    """封存檔名應符合 history_YYYYMMDD_HHMMSS[_N].json 格式且可被解析。"""
    hf = tmp_path / "history.json"
    a = agent_with_history(hf, [{"role": "user", "content": "x"}])
    archive = do_cls(a)
    assert archive is not None

    stem = archive.stem  # "history_20260522_143000"
    assert stem.startswith("history_")
    dt = stem[8:24]  # 取前 16 字元 "20260522_143000" 不管尾綴
    ts = f"{dt[:4]}-{dt[4:6]}-{dt[6:8]} {dt[9:11]}:{dt[11:13]}"
    assert len(ts) == 16
    assert ts[4] == "-" and ts[7] == "-" and ts[13] == ":"


def test_cls_same_second_no_overwrite(tmp_path: Path) -> None:
    """同秒兩次 /cls 不應覆蓋彼此的歸檔。"""
    hf = tmp_path / "history.json"

    a = agent_with_history(hf, [{"role": "user", "content": "first"}])
    archive1 = do_cls(a)

    a = agent_with_history(hf, [{"role": "user", "content": "second"}])
    archive2 = do_cls(a)

    assert archive1 is not None
    assert archive2 is not None
    assert archive1 != archive2
    assert archive1.exists()
    assert archive2.exists()


# ── /resume 還原測試 ──────────────────────────────────────────────────────────

def test_resume_restores_messages(tmp_path: Path) -> None:
    """/resume 後，agent.history 應含原始訊息。"""
    hf = tmp_path / "history.json"
    a = agent_with_history(hf, [{"role": "user", "content": "original"}])
    archive = do_cls(a)
    assert archive is not None

    do_resume_load(a, archive)

    assert "original" in user_contents(a)


def test_resume_archive_still_exists_after_load(tmp_path: Path) -> None:
    """/resume 是 copy，不是 move：歸檔應繼續存在。"""
    hf = tmp_path / "history.json"
    a = agent_with_history(hf, [{"role": "user", "content": "x"}])
    archive = do_cls(a)
    assert archive is not None

    do_resume_load(a, archive)

    assert archive.exists()


def test_resume_index_order_newest_first(tmp_path: Path) -> None:
    """list_archives 回傳應是最新在前（index 0 = 最近）。"""
    hf = tmp_path / "history.json"
    archives: list[Path] = []
    for i in range(3):
        a = agent_with_history(hf, [{"role": "user", "content": f"msg{i}"}])
        arch = do_cls(a)
        assert arch is not None
        archives.append(arch)

    found = list_archives(Agent(model="gemma3:4b", history_file=hf))
    assert len(found) == 3
    # 最新 = 最後建立的 = archives[2]
    assert found[0].name == archives[2].name


# ── resumed_from 旗標行為測試 ─────────────────────────────────────────────────

def test_cls_after_resume_without_new_messages_deletes_not_archives(tmp_path: Path) -> None:
    """/resume 後未新增訊息就 /cls：應直接刪除，不建新歸檔。"""
    hf = tmp_path / "history.json"
    a = agent_with_history(hf, [{"role": "user", "content": "A"}])
    archive = do_cls(a)
    assert archive is not None

    do_resume_load(a, archive)
    resumed_from = archive  # 模擬 self._resumed_from

    result = do_cls(a, resumed_from=resumed_from)

    assert result is None  # 沒有建新歸檔
    archives_after = list_archives(a)
    assert len(archives_after) == 1  # 只有原本那一個
    assert archive.exists()          # 原歸檔仍在


def test_cls_after_resume_with_new_messages_creates_new_archive(tmp_path: Path) -> None:
    """/resume 後新增訊息再 /cls：應建含新舊訊息的歸檔。"""
    hf = tmp_path / "history.json"
    a = agent_with_history(hf, [{"role": "user", "content": "original"}])
    archive1 = do_cls(a)
    assert archive1 is not None

    do_resume_load(a, archive1)
    # 新增訊息（模擬 _resumed_from 被清除）
    a.history.append({"role": "user", "content": "new message"})
    a.save_history()
    # resumed_from = None → 使用者已送出新訊息，正常歸檔
    archive2 = do_cls(a, resumed_from=None)

    assert archive2 is not None
    assert archive2 != archive1

    data = json.loads(archive2.read_text())
    contents = [m["content"] for m in data if m.get("role") == "user"]
    assert "original" in contents
    assert "new message" in contents


# ── 多次封存/還原循環 ─────────────────────────────────────────────────────────

def test_multiple_cls_resume_cycles(tmp_path: Path) -> None:
    """多次 /cls → /resume 循環後，每次都能取回正確訊息。"""
    hf = tmp_path / "history.json"

    a = agent_with_history(hf, [{"role": "user", "content": "session A"}])
    do_cls(a)

    a = agent_with_history(hf, [{"role": "user", "content": "session B"}])
    do_cls(a)

    archives = list_archives(a)
    assert len(archives) == 2

    # resume session A（較舊，index 1）
    do_resume_load(a, archives[1])
    assert "session A" in user_contents(a)

    # resume session B（較新，index 0）
    do_resume_load(a, archives[0])
    assert "session B" in user_contents(a)


def test_resume_does_not_cross_contaminate(tmp_path: Path) -> None:
    """resume session A 後，不應看到 session B 的訊息。"""
    hf = tmp_path / "history.json"

    a = agent_with_history(hf, [{"role": "user", "content": "session A only"}])
    do_cls(a)

    a = agent_with_history(hf, [{"role": "user", "content": "session B only"}])
    do_cls(a)

    archives = list_archives(a)
    assert len(archives) == 2

    do_resume_load(a, archives[1])  # load A

    contents = user_contents(a)
    assert "session A only" in contents
    assert "session B only" not in contents


# ── resume 切換前自動歸檔（回歸測試） ────────────────────────────────────────

def test_resume_switch_auto_archives_new_messages(tmp_path: Path) -> None:
    """resume A → 說話 → resume B：切換前應自動歸檔含新訊息的 session A。"""
    hf = tmp_path / "history.json"

    # 建立並歸檔 A、B
    a = agent_with_history(hf, [{"role": "user", "content": "session A"}])
    archive_a = do_cls(a)
    assert archive_a is not None

    a = agent_with_history(hf, [{"role": "user", "content": "session B"}])
    archive_b = do_cls(a)
    assert archive_b is not None

    # resume A → 送出新訊息（_resumed_from 清為 None，如 _send 所為）
    do_resume_load(a, archive_a, resumed_from=archive_a)  # 模擬 resume，_resumed_from=archive_a
    a.history.append({"role": "user", "content": "new after resume A"})
    a.save_history()
    # _send 完成後 _resumed_from 已清為 None

    # 切換到 B（resumed_from=None 表示剛送過訊息）
    do_resume_load(a, archive_b, resumed_from=None)

    # 應該有 3 個歸檔：原始 A、原始 B、自動歸檔的 A+新訊息
    all_archives = list_archives(a)
    assert len(all_archives) == 3, f"期望 3 個歸檔，實際有 {len(all_archives)}"

    # 目前 history 應是 session B
    assert "session B" in user_contents(a)
    assert "new after resume A" not in user_contents(a)

    # 最新歸檔（auto-archive）應含 session A 原始訊息和新訊息
    newest = all_archives[0]
    data = json.loads(newest.read_text())
    contents = [m["content"] for m in data if m.get("role") == "user"]
    assert "session A" in contents
    assert "new after resume A" in contents


def test_resume_switch_without_messages_no_extra_archive(tmp_path: Path) -> None:
    """resume A → 不說話 → resume B：不應產生額外歸檔。"""
    hf = tmp_path / "history.json"

    a = agent_with_history(hf, [{"role": "user", "content": "session A"}])
    archive_a = do_cls(a)
    assert archive_a is not None

    a = agent_with_history(hf, [{"role": "user", "content": "session B"}])
    archive_b = do_cls(a)
    assert archive_b is not None

    # resume A → 未說話 → resume B（resumed_from 仍為 archive_a）
    do_resume_load(a, archive_a, resumed_from=archive_a)
    do_resume_load(a, archive_b, resumed_from=archive_a)

    # 歸檔數應仍為 2
    all_archives = list_archives(a)
    assert len(all_archives) == 2
