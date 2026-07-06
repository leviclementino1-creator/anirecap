"""Extração de thumbnails do .mkv pra preview do plano de cortes.

Cada frame vira um .jpg pequeno em %TEMP%\\ancopy\\cache\\thumbs\\<mkv_key>\\.
Extração usa fast-seek (-ss antes do -i) — ~100-300ms por frame. O cache é
keyed por (caminho + tamanho do mkv, timestamp em décimos), então re-abrir
o editor do plano é instantâneo.
"""
import hashlib
import os
import subprocess
import tempfile

from utils.binaries import find_binary

_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
_CACHE_DIR = os.path.join(tempfile.gettempdir(), "ancopy", "cache", "thumbs")

# Offset pequeno após o timestamp pedido: scene changes marcam o PRIMEIRO
# frame da cena nova, que às vezes ainda é transição (fade/mistura). +0.2s
# entrega um frame já estável da cena.
_SEEK_OFFSET = 0.2


def _mkv_key(mkv_path: str) -> str:
    try:
        size = os.path.getsize(mkv_path)
    except OSError:
        size = 0
    h = hashlib.sha256()
    h.update(mkv_path.encode("utf-8"))
    h.update(str(size).encode("utf-8"))
    return h.hexdigest()[:16]


def thumb_path(mkv_path: str, t: float) -> str:
    """Caminho determinístico do thumbnail pro timestamp `t` (segundos)."""
    return os.path.join(
        _CACHE_DIR, _mkv_key(mkv_path), f"{int(round(t * 10)):08d}.jpg",
    )


def extract_thumb(
    mkv_path: str,
    t: float,
    binaries_dir: str = "",
    width: int = 384,
) -> str | None:
    """Extrai 1 frame em `t` segundos como jpg. Devolve o caminho ou None.

    Cache HIT devolve direto sem chamar ffmpeg.
    """
    out = thumb_path(mkv_path, t)
    if os.path.isfile(out):
        return out
    os.makedirs(os.path.dirname(out), exist_ok=True)

    try:
        ffmpeg = find_binary("ffmpeg", binaries_dir)
    except Exception:
        return None

    cmd = [
        ffmpeg, "-y",
        "-ss", f"{max(0.0, t + _SEEK_OFFSET):.3f}",
        "-i", mkv_path,
        "-frames:v", "1",
        "-vf", f"scale={width}:-2",
        "-q:v", "4",
        out,
    ]
    r = subprocess.run(
        cmd, capture_output=True,
        creationflags=_NO_WINDOW,
    )
    if r.returncode != 0 or not os.path.isfile(out) or os.path.getsize(out) == 0:
        try:
            os.remove(out)
        except OSError:
            pass
        return None
    return out
