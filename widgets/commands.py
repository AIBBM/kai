"""Command palette provider."""
from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING

from textual.command import Hit, Hits, Provider

from core.models import REGISTRY

if TYPE_CHECKING:
    from app import ChatApp


class ChatCommands(Provider):
    def _all_hits(self, app: ChatApp) -> list[Hit]:
        current = app.agent.profile.name
        hits = [Hit(1.0, "清除對話", app.action_clear, help="清除所有訊息與對話記錄")]
        for model_name in REGISTRY:
            is_cur = model_name == current
            hits.append(
                Hit(
                    1.0 if is_cur else 0.8,
                    f"切換模型  {model_name}{'  ✓' if is_cur else ''}",
                    partial(app.action_switch_model, model_name),
                    help="目前使用中" if is_cur else f"切換到 {model_name}",
                )
            )
        return hits

    async def discover(self) -> Hits:
        for hit in self._all_hits(self.app):  # type: ignore[arg-type]
            yield hit

    async def search(self, query: str) -> Hits:
        app: ChatApp = self.app  # type: ignore[assignment]
        matcher = self.matcher(query)
        for hit in self._all_hits(app):
            label = (
                hit.match_display
                if isinstance(hit.match_display, str)
                else str(hit.match_display)
            )
            score = matcher.match(label)
            if score > 0:
                yield Hit(score, matcher.highlight(label), hit.command, help=hit.help)
