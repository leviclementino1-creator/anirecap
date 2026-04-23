"""Cache em disco para evitar regerar conteúdo durante testes.

Cobre:
- LLM (resumo, roteiro short): chave = hash(kind, model, prompt, input)
- TTS (mp3 + alignment): chave = hash(text, voice_id, model_id, speed, silence_cut)

Ativado por um switch em settings (`use_cache`). Tudo em `%TEMP%\\ancopy\\cache\\`.
"""
import hashlib
import json
import os
import shutil
import tempfile
from typing import List, Optional, Tuple

CACHE_ROOT = os.path.join(tempfile.gettempdir(), "ancopy", "cache")


def _hash_parts(parts: List[str]) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update((p or "").encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()[:20]


# ----------------------------------------------------------------- LLM
def _llm_path(key: str) -> str:
    return os.path.join(CACHE_ROOT, "llm", f"{key}.json")


def get_llm(parts: List[str]) -> Optional[str]:
    key = _hash_parts(parts)
    path = _llm_path(key)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f).get("content") or None
    except Exception:
        return None


def set_llm(parts: List[str], content: str) -> None:
    key = _hash_parts(parts)
    path = _llm_path(key)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                {"content": content, "preview": content[:120]},
                f, ensure_ascii=False, indent=2,
            )
    except Exception:
        pass


# ----------------------------------------------------------------- TTS
def _tts_dir(key: str) -> str:
    return os.path.join(CACHE_ROOT, "tts", key)


def get_tts(parts: List[str]) -> Tuple[Optional[str], Optional[str]]:
    """Devolve (audio_path, alignment_path) se presentes em cache, senão (None, None)."""
    key = _hash_parts(parts)
    d = _tts_dir(key)
    mp3 = os.path.join(d, "narration.mp3")
    align = os.path.join(d, "narration.alignment.json")
    if os.path.isfile(mp3) and os.path.isfile(align):
        return mp3, align
    return None, None


def set_tts(parts: List[str], audio_src: str, alignment_src: str) -> Tuple[str, str]:
    """Copia os arquivos pro cache. Devolve os paths cachados."""
    key = _hash_parts(parts)
    d = _tts_dir(key)
    os.makedirs(d, exist_ok=True)
    mp3 = os.path.join(d, "narration.mp3")
    align = os.path.join(d, "narration.alignment.json")
    try:
        shutil.copy(audio_src, mp3)
        shutil.copy(alignment_src, align)
    except Exception:
        pass
    return mp3, align


# ----------------------------------------------------------------- utilitário
def size_bytes() -> int:
    """Tamanho total do cache em bytes (pra mostrar no settings)."""
    total = 0
    if not os.path.isdir(CACHE_ROOT):
        return 0
    for root, _, files in os.walk(CACHE_ROOT):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def clear_all() -> None:
    """Apaga tudo em %TEMP%\\ancopy\\cache\\."""
    if os.path.isdir(CACHE_ROOT):
        shutil.rmtree(CACHE_ROOT, ignore_errors=True)
