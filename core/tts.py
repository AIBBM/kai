"""TTS 模組 — edge-tts（雲端）或 kokoro-onnx（本地）生成音訊。"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from loguru import logger

_PLAYERS = ["mpv", "ffplay", "mplayer", "mpg123"]

# kokoro 模型單例（第一次呼叫時載入，之後重用）
_kokoro_model: Any = None


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


async def _play_file(path: str, player: str = "auto") -> None:
    """播放音訊檔；CancelledError 時 kill 子程序。"""
    resolved = player if player != "auto" else _detect_player()
    proc: asyncio.subprocess.Process | None = None
    try:
        if resolved:
            cmd = _player_cmd(resolved, path)
        else:
            # WSL2 PowerShell fallback
            try:
                win_path = subprocess.check_output(
                    ["wslpath", "-w", path], stderr=subprocess.DEVNULL
                ).decode().strip()
            except Exception:
                logger.warning("tts: wslpath 轉換失敗，無法播放")
                return
            # WAV/MP3 file size 估算播放時長
            size = Path(path).stat().st_size
            suffix = Path(path).suffix.lower()
            if suffix == ".wav":
                # WAV PCM 24kHz 32-bit float mono = 96000 bytes/sec
                duration_ms = max(1500, int(size / 96) + 500)
            else:
                # MP3 ~24kbps CBR = 3000 bytes/sec
                duration_ms = max(1500, int(size / 3) + 800)
            win_path_fwd = win_path.replace(chr(92), "/")
            script = (
                "Add-Type -AssemblyName presentationCore; "
                "$m = New-Object System.Windows.Media.MediaPlayer; "
                f"$m.Open([Uri]'file:///{win_path_fwd}'); "
                "$m.Play(); "
                f"Start-Sleep -Milliseconds {duration_ms}; "
                "$m.Close()"
            )
            cmd = ["powershell.exe", "-NoProfile", "-Command", script]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
    except asyncio.CancelledError:
        if proc and proc.returncode is None:
            proc.kill()
        raise
    except Exception as e:
        logger.warning(f"tts 播放失敗：{e}")


def _clean_for_tts(text: str) -> str:
    """移除 markdown 格式符號與不應朗讀的符號。"""
    text = re.sub(r"```[\s\S]*?```", "", text)
    text = re.sub(r"`[^`]+`", "", text)
    text = re.sub(r"\*{1,3}([^*]+)\*{1,3}", r"\1", text)
    text = re.sub(r"_{1,3}([^_]+)_{1,3}", r"\1", text)
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^[\s]*[•\-\*]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^[\s]*\d+[.)]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"[←→↑↓↖↗↘↙⇒⇐⇔▶▷◀◁►◄]", "", text)
    text = re.sub(r"[\U0001F000-\U0001FFFF\U00002600-\U000027BF]", "", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


_KOKORO_BASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
_KOKORO_CACHE = Path.home() / ".cache" / "kokoro-onnx"
_KOKORO_MODEL_FILE = "kokoro-v1.0.int8.onnx"   # int8 CPU-friendly (~80MB)
_KOKORO_VOICES_FILE = "voices-v1.0.bin"


def _download_kokoro_file(filename: str) -> Path:
    import urllib.request
    dest = _KOKORO_CACHE / filename
    if dest.exists():
        return dest
    _KOKORO_CACHE.mkdir(parents=True, exist_ok=True)
    url = f"{_KOKORO_BASE}/{filename}"
    logger.info(f"tts: 下載 {filename}…")
    urllib.request.urlretrieve(url, dest)
    logger.info(f"tts: {filename} 下載完成")
    return dest


def _load_kokoro() -> Any:
    """載入 kokoro-onnx 模型（首次自動下載 ~100MB 至 ~/.cache/kokoro-onnx/）。"""
    global _kokoro_model
    if _kokoro_model is not None:
        return _kokoro_model
    try:
        from kokoro_onnx import Kokoro
    except ImportError:
        raise RuntimeError("kokoro-onnx 未安裝：uv add kokoro-onnx soundfile")

    model_path = _download_kokoro_file(_KOKORO_MODEL_FILE)
    voices_path = _download_kokoro_file(_KOKORO_VOICES_FILE)
    logger.info("tts: 初始化 kokoro 模型…")
    _kokoro_model = Kokoro(str(model_path), str(voices_path))
    logger.info("tts: kokoro 就緒")
    return _kokoro_model


async def _speak_kokoro(
    text: str,
    voice: str = "af_sky",
    lang: str = "en-us",
    player: str = "auto",
) -> None:
    """kokoro-onnx 本地 TTS。模型在 thread executor 跑，不阻塞 event loop。"""
    import soundfile as sf

    def _generate() -> tuple[Any, int]:
        kokoro = _load_kokoro()
        return kokoro.create(text, voice=voice, speed=1.0, lang=lang)

    try:
        samples, sample_rate = await asyncio.get_event_loop().run_in_executor(None, _generate)
    except Exception as e:
        logger.warning(f"tts kokoro 生成失敗：{e}")
        return

    fd, tmp = tempfile.mkstemp(suffix=".wav", prefix="kai_tts_")
    os.close(fd)
    try:
        sf.write(tmp, samples, sample_rate)
        await _play_file(tmp, player)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.warning(f"tts kokoro 播放失敗：{e}")
    finally:
        Path(tmp).unlink(missing_ok=True)


async def speak(
    text: str,
    voice: str = "en-US-AriaNeural",
    player: str = "auto",
    engine: str = "edge-tts",
    lang: str = "en-us",
) -> None:
    """語音播放。engine='kokoro' 走本地推理，否則走 edge-tts（需網路）。"""
    text = _clean_for_tts(text)
    if not text:
        return

    if engine == "kokoro":
        await _speak_kokoro(text, voice=voice, lang=lang, player=player)
        return

    # edge-tts
    try:
        import edge_tts
    except ImportError:
        logger.warning("tts: edge-tts 未安裝，執行 `uv add edge-tts`")
        return

    fd, tmp = tempfile.mkstemp(suffix=".mp3", prefix="kai_tts_")
    os.close(fd)
    try:
        communicate = edge_tts.Communicate(text, voice)
        await communicate.save(tmp)
        await _play_file(tmp, player)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.warning(f"tts 失敗：{e}")
    finally:
        Path(tmp).unlink(missing_ok=True)
