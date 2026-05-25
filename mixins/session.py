"""SessionMixin — /nickname, /cd, /compact, /cls, /resume, /model 指令邏輯。"""
from __future__ import annotations

import json
import os
import shutil
from datetime import datetime
from pathlib import Path

from loguru import logger

from textual.containers import VerticalScroll
from textual.widgets import Static, TabbedContent

from core.agent import Agent
from core.config import _normalize, get_nickname
from core.models import REGISTRY
from widgets.input import HistoryInput
from widgets.sidebar import Sidebar
from widgets.tree import FileTree

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app import ChatApp as _MixinBase
else:
    _MixinBase = object  # type: ignore[assignment,misc]


class SessionMixin(_MixinBase):
    def _do_nickname(self, arg: str) -> None:
        model = self.agent.profile.name
        key = f"NICKNAME_{_normalize(model)}"
        if not arg:
            current = get_nickname(model)
            shown = current if current != model else "（未設定）"
            self.notify(f"{model}\n.env key：{key}\n目前暱稱：{shown}", timeout=6)
            return
        env_path = Path(".env")
        lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
        replaced = False
        for i, line in enumerate(lines):
            if line.startswith(f"{key}=") or line.startswith(f"{key} ="):
                lines[i] = f"{key}={arg}"
                replaced = True
                break
        if not replaced:
            lines.append(f"{key}={arg}")
        env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.environ[key] = arg
        self.agent._rebuild_system_prompt()
        self.agent.save_history()
        prompt = self.query_one("#prompt", HistoryInput)
        prompt.placeholder = f"傳訊息給 {arg}..."
        self.sub_title = arg
        self.notify(f"✅ {model} 暱稱設為「{arg}」", timeout=3)

    async def _do_cd(self, arg: str) -> None:
        target = Path(self._prev_cwd if arg == "-" else arg).expanduser().resolve()
        if not target.is_dir():
            self.notify(f"目錄不存在：{target}", severity="error")
            return
        self.agent.save_history()
        self._prev_cwd = os.getcwd()
        os.chdir(target)
        # reset session state for new directory
        self._resumed_from = None
        self._tab_resumed_from["chat"] = None
        self._session_name = ""
        self._tab_session_names.pop("chat", None)
        # check if new directory has a persisted resume state
        _state = self.agent._history_file.resolve().parent / ".history" / ".resumed_from"
        if _state.exists():
            _arch = _state.parent / _state.read_text(encoding="utf-8").strip()
            if _arch.exists():
                self._resumed_from = _arch
                self._tab_resumed_from["chat"] = _arch
                self._load_session_name_from_archive(_arch)
        self.query_one("#sb-cwd", Static).update(str(target))
        self.query_one("#file-tree", FileTree).refresh_tree()
        self.agent.reset_history()
        await self.query_one("#messages", VerticalScroll).remove_children()
        self._render_history()
        self._update_subtitle()
        self.notify(f"已切換至 {target}", severity="information")

    async def action_compact(self) -> None:
        if not self.agent._gemini and not self.agent._deepseek:
            self.notify("需要 GEMINI_API_KEY 或 DEEPSEEK_API_KEY 才能使用 compact", severity="warning")
            return
        msgs = self._active_msgs()
        await msgs.mount(Static("⚡ 正在用 Gemini 壓縮對話歷史…", classes="error-msg", markup=False))
        msgs.scroll_end(animate=False)
        try:
            await self.agent.compact_history()
            await msgs.remove_children()
            await self._render_history_into(msgs, self.agent)
            self.notify("✅ 對話已壓縮", severity="information")
        except Exception as e:
            self.notify(f"compact 失敗：{e}", severity="error")

    async def action_clear_screen(self) -> None:
        await self._active_msgs().remove_children()

    # ── session 命名 ──────────────────────────────────────────────────────────

    def _update_subtitle(self) -> None:
        display = self.agent.display_name
        name = getattr(self, "_session_name", "")
        self.sub_title = f"{display} · {name}" if name else display

    def _current_tab_id(self) -> str:
        from textual.widgets import TabbedContent
        try:
            return self.query_one("#center-tabs", TabbedContent).active or "chat"
        except Exception:
            return "chat"

    def _save_session_name_for_archive(self, archive: Path) -> None:
        """把 _session_name 寫入 .names.json，然後清除。"""
        name = getattr(self, "_session_name", "")
        if name:
            names_file = archive.parent / ".names.json"
            try:
                names: dict = json.loads(names_file.read_text(encoding="utf-8")) if names_file.exists() else {}
                names[archive.name] = name
                names_file.write_text(json.dumps(names, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                logger.warning(f"無法儲存 session 名稱：{archive.name}")
            self._session_name = ""
            self._tab_session_names.pop(self._current_tab_id(), None)
            self._update_subtitle()
        # 名稱已歸入 .names.json，刪除暫存的 .current_name
        try:
            self._current_name_file().unlink(missing_ok=True)
        except Exception:
            pass

    def _load_session_name_from_archive(self, archive: Path) -> None:
        """從 .names.json 讀取歸檔名稱並設入 _session_name。"""
        names_file = archive.parent / ".names.json"
        try:
            names: dict = json.loads(names_file.read_text(encoding="utf-8")) if names_file.exists() else {}
            self._session_name = names.get(archive.name, "")
        except Exception:
            self._session_name = ""
        self._tab_session_names[self._current_tab_id()] = self._session_name
        self._update_subtitle()

    def _current_name_file(self) -> Path:
        """回傳目前 agent history 所在目錄下的 .history/.current_name 路徑。"""
        return self.agent._history_file.resolve().parent / ".history" / ".current_name"

    def _do_rename(self, arg: str) -> None:
        name = getattr(self, "_session_name", "")
        if not arg:
            shown = name or "（未命名）"
            self.notify(f"目前 session 名稱：{shown}\n用法：/rename <名稱>", timeout=5)
            return
        self._session_name = arg
        self._tab_session_names[self._current_tab_id()] = arg
        # 持久化名稱，讓重啟後也能還原
        try:
            _nf = self._current_name_file()
            _nf.parent.mkdir(exist_ok=True)
            _nf.write_text(arg, encoding="utf-8")
        except Exception:
            pass
        self._update_subtitle()
        self.notify(f"Session 命名為「{arg}」，歸檔時自動儲存", timeout=3)

    # ── /cls ─────────────────────────────────────────────────────────────────

    async def action_clear(self) -> None:
        # 快照：await 期間 self.agent 可能因 tab 事件被替換，固定操作當下的 agent/msgs
        agent = self.agent
        msgs = self._active_msgs()
        real_msgs = [m for m in agent.history if m.get("role") != "system"]
        if not real_msgs:
            self.notify("沒有對話記錄，無需清除", severity="information", timeout=2)
            return
        await msgs.remove_children()
        hist_file = agent._history_file.resolve()
        hist_dir = hist_file.parent / ".history"
        if hist_file.exists() and self._resumed_from is None:
            hist_dir.mkdir(exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            archive = hist_dir / f"history_{ts}.json"
            n = 1
            while archive.exists():  # 同秒多次 /cls 時避免覆蓋
                archive = hist_dir / f"history_{ts}_{n}.json"
                n += 1
            hist_file.rename(archive)
            self._save_session_name_for_archive(archive)
            self.notify(f"歷史已歸檔至 {hist_dir.name}/{archive.name}", timeout=3)
        elif self._resumed_from is not None:
            hist_file.unlink(missing_ok=True)
            self._session_name = ""
            self._tab_session_names.pop(self._current_tab_id(), None)
            try:
                self._current_name_file().unlink(missing_ok=True)
            except Exception:
                pass
            self.notify("已清除（resume 後未新增對話，不重複歸檔）", timeout=3)
        self._resumed_from = None
        self._tab_resumed_from.pop(self._current_tab_id(), None)
        agent.history = []
        agent._rebuild_system_prompt()
        agent.save_history()
        self._update_subtitle()
        self.query_one("#file-tree", FileTree).refresh_tree()

    async def _handle_resume(self, arg: str) -> None:
        from widgets.resume import ResumeScreen
        hist_dir = self.agent._history_file.resolve().parent / ".history"
        archives = sorted(hist_dir.glob("history_*.json"), reverse=True) if hist_dir.exists() else []
        if not archives:
            self.notify("沒有任何歸檔 session", severity="warning")
            return
        if arg:
            try:
                idx = int(arg) - 1
                archive = archives[idx]
            except (ValueError, IndexError):
                self.notify(f"編號無效，共 {len(archives)} 個歸檔", severity="error")
                return
            await self._resume_load(archive)
            return

        names_file = hist_dir / ".names.json"
        entries = []
        for p in archives:
            try:
                msgs_data = json.loads(p.read_text(encoding="utf-8"))
                user_msgs = [m for m in msgs_data if m.get("role") == "user"]
                count = len(user_msgs)
                last = user_msgs[-1]["content"] if user_msgs else ""
                if isinstance(last, list):
                    last = next((b["text"] for b in last if b.get("type") == "text"), "")
                preview = str(last)[:50].replace("\n", " ")
                stem = p.stem
                try:
                    dt = stem[8:]
                    ts = f"{dt[:4]}-{dt[4:6]}-{dt[6:8]} {dt[9:11]}:{dt[11:13]}"
                except Exception:
                    ts = stem
            except Exception:
                logger.warning(f"resume: 無法解析歸檔 {p.name}")
                count, preview, ts = 0, "(無法讀取)", p.name
            entries.append({"ts": ts, "count": count, "preview": preview})

        async def _on_result(idx: int | None) -> None:
            if idx is not None:
                await self._resume_load(archives[idx])

        self.push_screen(ResumeScreen(entries, archives, names_file, self._resumed_from), _on_result)

    async def _resume_load(self, archive: Path) -> None:
        # 切換前先處理當前 session
        real_msgs = [m for m in self.agent.history if m.get("role") != "system"]
        hist_file = self.agent._history_file.resolve()
        hist_dir = hist_file.parent / ".history"
        if real_msgs and self._resumed_from is None and hist_file.exists():
            # 有新訊息且非 resume 狀態 → 自動歸檔，避免切換時遺失
            hist_dir.mkdir(exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            auto_arch = hist_dir / f"history_{ts}.json"
            n = 1
            while auto_arch.exists():
                auto_arch = hist_dir / f"history_{ts}_{n}.json"
                n += 1
            hist_file.rename(auto_arch)
            self._save_session_name_for_archive(auto_arch)
            self.notify(f"已自動歸檔：{auto_arch.name}", timeout=3)
        elif self._resumed_from is not None and hist_file.exists():
            # resume 後未新增訊息直接切換 → 刪除，不重複歸檔
            hist_file.unlink(missing_ok=True)
            self._session_name = ""

        try:
            shutil.copy2(archive, self.agent._history_file)
        except Exception as e:
            self.notify(f"載入失敗：{e}", severity="error")
            return
        # 持久化 resume 狀態，讓重開 app 後仍知道 history.json 是 resume 來的
        state_file = self.agent._history_file.resolve().parent / ".history" / ".resumed_from"
        state_file.parent.mkdir(exist_ok=True)
        state_file.write_text(archive.name, encoding="utf-8")
        msgs = self._active_msgs()
        await msgs.remove_children()
        self.agent.reset_history()
        await self._render_history_into(msgs, self.agent)
        self._resumed_from = archive
        self._tab_resumed_from[self._current_tab_id()] = archive
        self._load_session_name_from_archive(archive)
        name_display = getattr(self, "_session_name", "") or archive.name
        self.notify(f"已載入：{name_display}（有新對話後才會重新歸檔）", timeout=4)

    async def action_switch_model(self, model_name: str) -> None:
        if model_name not in REGISTRY:
            self.notify(f"未知模型：{model_name}", severity="error")
            return
        if model_name == self.agent.profile.name:
            self.notify("已是目前模型", severity="information")
            return
        old_history = self.agent.history[:]
        old_mcp = self.agent.mcp_client
        old_ctx = getattr(self.agent, "mcp_context", "")
        self.agent = Agent(model=model_name)
        self.agent.history = old_history
        self.agent.mcp_client = old_mcp
        self.agent.mcp_context = old_ctx
        self.agent._rebuild_system_prompt()
        self._think_override = None
        tc = self.query_one("#center-tabs", TabbedContent)
        if tc.active == "chat":
            self._main_agent = self.agent
        self._sync_cap_bar()
        prompt = self.query_one("#prompt", HistoryInput)
        prompt.placeholder = f"傳訊息給 {get_nickname(model_name)}..."
        self.sub_title = get_nickname(model_name)
        self.query_one("#sidebar", Sidebar).set_model(model_name)
        self.notify(f"已切換至 {model_name}", severity="information")
