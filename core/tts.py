"""TTS 模組 — edge-tts 生成音訊，自動選擇本地播放器或 PowerShell（WSL2）。"""
from __future__ import annotations

import asyncio
import shutil
import subprocess
import tempfile
from pathlib import Path

from loguru import logger

# 播放器偵測順序（命令名稱）
_PLAYERS = ["mpv", "ffplay", "mplayer", "mpg123"]


def _detect_player() -> str | None:
    for p in _PLAYERS:
        if shutil.which(p):
            return p
    return None


def _player_cmd(player: str, path: str) -> list[str]:
    match player:
        case "mpv":
            return ["mpv", "--no-terminal", "--really-quiet", path]
        case "ffplay":
            return ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", path]
        case "mplayer":
            return ["mplayer", "-really-quiet", path]
        case "mpg123":
            return ["mpg123", "-q", path]
        case _:
            return [player, path]


async def _play_powershell(mp3_path: str) -> None:
    """WSL2 fallback：透過 PowerShell 用 Windows MediaPlayer 播放。"""
    try:
        win_path = subprocess.check_output(
            ["wslpath", "-w", mp3_path], stderr=subprocess.DEVNULL
        ).decode().strip()
    except Exception:
        logger.warning("tts: wslpath 轉換失敗，無法播放")
        return
    script = (
        "Add-Type -AssemblyName presentationCore; "
        "$m = New-Object System.Windows.Media.MediaPlayer; "
        f"$m.Open([Uri]'file:///{win_path.replace(chr(92), '/')}'); "
        "$m.Play(); "
        "Start-Sleep -Milliseconds 500; "  # 等 metadata 載入
        "$dur = $m.NaturalDuration.TimeSpan.TotalMilliseconds; "
        "if ($dur -gt 0) { Start-Sleep -Milliseconds $dur } else { Start-Sleep -Seconds 10 }; "
        "$m.Close()"
    )
    proc = await asyncio.create_subprocess_exec(
        "powershell.exe", "-NoProfile", "-Command", script,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.wait()


async def speak(text: str, voice: str = "en-US-AriaNeural", player: str = "auto") -> None:
    """生成語音並播放。不阻塞呼叫方（在 asyncio 背景執行）。"""
    try:
        import edge_tts
    except ImportError:
        logger.warning("tts: edge-tts 未安裝，執行 `uv add edge-tts`")
        return

    if not text.strip():
        return

    tmp = tempfile.mktemp(suffix=".mp3", prefix="kai_tts_")
    try:
        communicate = edge_tts.Communicate(text, voice)
        await communicate.save(tmp)

        resolved_player = player if player != "auto" else _detect_player()
        if resolved_player:
            cmd = _player_cmd(resolved_player, tmp)
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
        else:
            await _play_powershell(tmp)
    except Exception as e:
        logger.warning(f"tts 失敗：{e}")
    finally:
        Path(tmp).unlink(missing_ok=True)
