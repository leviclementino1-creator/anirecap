"""Detecção de mudanças de cena no .mkv original via ffmpeg nativo.

Rodar `ffmpeg -vf "select='gt(scene,0.35)',metadata=print" -an -f null -`
emite no stderr uma linha `pts_time:X.Y` pra cada frame onde a diff visual
ultrapassa o threshold. Parseio e devolvo lista ordenada de segundos.

Cacheia em disco: a mesma análise é determinística e 24min de episódio
leva ~30s pra rodar.
"""
import hashlib
import json
import os
import re
import subprocess
import tempfile
from typing import List

from utils.binaries import find_binary

_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
_CACHE_DIR = os.path.join(tempfile.gettempdir(), "ancopy", "cache", "scenes")
_PTS_RE = re.compile(r"pts_time:([\d.]+)")


def _cache_key(mkv_path: str, threshold: float) -> str:
    """Hash inclui caminho + tamanho do arquivo + threshold. Se o mkv mudar, invalida."""
    try:
        size = os.path.getsize(mkv_path)
    except OSError:
        size = 0
    h = hashlib.sha256()
    h.update(mkv_path.encode("utf-8"))
    h.update(str(size).encode("utf-8"))
    h.update(f"{threshold:.3f}".encode("utf-8"))
    return h.hexdigest()[:20]


def _cache_path(mkv_path: str, threshold: float) -> str:
    return os.path.join(_CACHE_DIR, _cache_key(mkv_path, threshold) + ".json")


def detect_scenes(
    mkv_path: str,
    threshold: float = 0.35,
    binaries_dir: str = "",
    use_cache: bool = True,
) -> List[float]:
    """Devolve lista ordenada de timestamps (segundos) onde há cena nova."""
    if use_cache:
        cache_file = _cache_path(mkv_path, threshold)
        if os.path.isfile(cache_file):
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    return [float(t) for t in data]
            except Exception:
                pass

    ffmpeg = find_binary("ffmpeg", binaries_dir)
    result = subprocess.run(
        [
            ffmpeg, "-i", mkv_path,
            "-vf", f"select='gt(scene,{threshold})',metadata=print",
            "-an", "-sn", "-f", "null", "-",
        ],
        capture_output=True, text=True,
        encoding="utf-8", errors="replace",
        creationflags=_NO_WINDOW,
    )
    timestamps: List[float] = []
    for line in (result.stderr or "").splitlines():
        m = _PTS_RE.search(line)
        if m:
            try:
                timestamps.append(float(m.group(1)))
            except ValueError:
                pass

    timestamps.sort()

    if use_cache:
        try:
            os.makedirs(_CACHE_DIR, exist_ok=True)
            with open(_cache_path(mkv_path, threshold), "w", encoding="utf-8") as f:
                json.dump(timestamps, f, indent=2)
        except Exception:
            pass

    return timestamps


def snap_to_scene(
    target_start: float,
    scenes: List[float],
    max_backward: float = 3.0,
    max_forward: float = 0.5,
) -> float:
    """Snap `target_start` pra mudança de cena mais próxima.

    Preferência: `backward` (cena anterior ao target), até `max_backward` segs.
    Se nenhuma serve, tenta `forward` (cena depois do target) até `max_forward`
    segs — uma pequena margem é aceitável pra sacrificar um pouco da fala em
    troca de começar num corte limpo.

    Se nem forward nem backward servem, devolve o target original.
    """
    if not scenes:
        return target_start

    best_back = None
    for s in scenes:
        if s > target_start:
            break
        if target_start - s <= max_backward:
            best_back = s

    if best_back is not None:
        return best_back

    for s in scenes:
        if s >= target_start:
            if s - target_start <= max_forward:
                return s
            break

    return target_start
