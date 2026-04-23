"""Pós-processamento da narração.

1. Silence cut via ffmpeg `silenceremove` (tira respirações/pausas longas).
2. Speed change via ffmpeg `atempo` (encadeado para cobrir 0.5x–100x).
3. Reajusta o alignment de caracteres do ElevenLabs para refletir as duas
   operações — crítico pras legendas word-level da Fase 6 não quebrarem.

Requer ffmpeg e ffprobe no PATH, em `binaries_dir`, ou empacotados.
"""
import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import List, Tuple

from core.tts import Alignment
from utils.binaries import find_binary

_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


@dataclass
class PostProcessStats:
    original_duration: float = 0.0
    final_duration: float = 0.0
    silences_removed_count: int = 0
    silences_removed_seconds: float = 0.0
    speed: float = 1.0


# -------------------------------------------------------------- silence cut
def _detect_silences(
    input_path: str,
    threshold_db: float,
    min_duration: float,
    binaries_dir: str,
) -> List[Tuple[float, float]]:
    """Devolve lista de (start, end) em segundos."""
    ffmpeg = find_binary("ffmpeg", binaries_dir)
    r = subprocess.run(
        [ffmpeg, "-i", input_path, "-af",
         f"silencedetect=n={threshold_db}dB:d={min_duration}",
         "-f", "null", "-"],
        capture_output=True, text=True,
        encoding="utf-8", errors="replace",
        creationflags=_NO_WINDOW,
    )
    segs: List[Tuple[float, float]] = []
    start = None
    for line in (r.stderr or "").splitlines():
        line = line.strip()
        if "silence_start" in line:
            try:
                start = float(line.split("silence_start:")[1].strip().split()[0])
            except Exception:
                start = None
        elif "silence_end" in line and start is not None:
            try:
                end = float(line.split("silence_end:")[1].split("|")[0].strip().split()[0])
                segs.append((start, end))
            except Exception:
                pass
            start = None
    return segs


def _trim_silences(
    input_path: str,
    output_path: str,
    interior_threshold_db: float,
    stop_duration: float,
    keep_silence: float,
    binaries_dir: str,
) -> None:
    """Single-pass `silenceremove` com onset-safe defaults.

    Parâmetros e seus papéis:
    - `stop_duration=N`  → só detecta pausas ≥ N segundos (pausas menores ficam)
    - `stop_silence=M`   → cada pausa detectada é reduzida para M segundos
    - `stop_threshold=-42dB` → QUEM protege os onsets. Consoantes suaves tipo
      "M" em "Mano" ou "ch" em "chat" ficam acima de -42dB e são tratadas como
      fala, não silêncio — o filtro para de cortar antes de entrar nelas.

    Por design, N > M: pausas longas ficam mais curtas (cut), mas pausas já
    curtas ficam intactas. O threshold cuida da qualidade dos onsets.
    """
    ffmpeg = find_binary("ffmpeg", binaries_dir)
    flt = (
        f"silenceremove=stop_periods=-1:"
        f"stop_duration={stop_duration}:stop_silence={keep_silence}:"
        f"stop_threshold={interior_threshold_db}dB"
    )
    r = subprocess.run(
        [ffmpeg, "-y", "-i", input_path, "-af", flt, output_path],
        capture_output=True, text=True,
        encoding="utf-8", errors="replace",
        creationflags=_NO_WINDOW,
    )
    if r.returncode != 0:
        raise RuntimeError(f"silenceremove falhou: {r.stderr[:300]}")


# -------------------------------------------------------------- speed change
def _atempo_chain(speed: float) -> str:
    """`atempo` aceita 0.5–100 no ffmpeg moderno, mas encadeamos em passos de
    até 2x para melhor qualidade de áudio em valores extremos."""
    filters = []
    remaining = speed
    while remaining > 2.0:
        filters.append("atempo=2.0")
        remaining /= 2.0
    while remaining < 0.5:
        filters.append("atempo=0.5")
        remaining /= 0.5
    filters.append(f"atempo={remaining:.4f}")
    return ",".join(filters)


def _change_speed(
    input_path: str,
    output_path: str,
    speed: float,
    binaries_dir: str,
) -> None:
    ffmpeg = find_binary("ffmpeg", binaries_dir)
    r = subprocess.run(
        [ffmpeg, "-y", "-i", input_path, "-af", _atempo_chain(speed), output_path],
        capture_output=True, text=True,
        encoding="utf-8", errors="replace",
        creationflags=_NO_WINDOW,
    )
    if r.returncode != 0:
        raise RuntimeError(f"atempo falhou: {r.stderr[:300]}")


# -------------------------------------------------------------- ffprobe util
def _ffprobe_duration(path: str, binaries_dir: str) -> float:
    try:
        ffprobe = find_binary("ffprobe", binaries_dir)
    except Exception:
        return 0.0
    try:
        r = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            creationflags=_NO_WINDOW,
        )
        return float(r.stdout.strip())
    except Exception:
        return 0.0


# -------------------------------------------------------------- alignment
def _adjust_alignment(
    align: Alignment,
    silences: List[Tuple[float, float]],
    keep_silence: float,
    speed: float,
) -> Alignment:
    """Reescreve timestamps: cada silêncio ≥ stop_duration é reduzido para
    keep_silence; depois divide por speed.
    """
    silences_sorted = sorted(silences)

    def _t(t_orig: float) -> float:
        removed = 0.0
        for s, e in silences_sorted:
            if e <= t_orig:
                removed += max(0.0, (e - s) - keep_silence)
            elif s < t_orig < e:
                inside = max(0.0, t_orig - s - keep_silence)
                removed += min(inside, (e - s) - keep_silence)
                break
            else:
                break
        return max(0.0, (t_orig - removed) / speed)

    return Alignment(
        characters=list(align.characters),
        starts=[_t(t) for t in align.starts],
        ends=[_t(t) for t in align.ends],
    )


# -------------------------------------------------------------- orquestração
def postprocess(
    audio_path: str,
    alignment: Alignment,
    speed: float = 1.0,
    silence_cut: bool = True,
    interior_threshold_db: float = -42.0,  # onset-safe
    stop_duration: float = 0.30,           # só corta pausas ≥ 300ms
    keep_silence: float = 0.15,            # reduz cada pausa pra 150ms (mais ritmo)
    binaries_dir: str = "",
) -> Tuple[Alignment, PostProcessStats]:
    """Pós-processa `audio_path` in-place. Salva o original como `*.raw.mp3`.

    Devolve `(Alignment ajustado, estatísticas)`. Alignment.duration atualizado.
    """
    stats = PostProcessStats(
        original_duration=alignment.duration,
        speed=speed,
    )

    nothing_to_do = not silence_cut and abs(speed - 1.0) < 0.001
    if nothing_to_do:
        stats.final_duration = stats.original_duration
        return alignment, stats

    current = audio_path
    intermediates: List[str] = []
    silences: List[Tuple[float, float]] = []

    # 1. Silence cut (two-pass)
    if silence_cut:
        # Detecção com params de pass 2 (mais sensível) — garante que captamos
        # todas as pausas que serão de fato cortadas.
        silences = _detect_silences(
            audio_path, interior_threshold_db, stop_duration, binaries_dir,
        )
        tmp = audio_path + ".trim.mp3"
        _trim_silences(
            current, tmp,
            interior_threshold_db, stop_duration, keep_silence,
            binaries_dir,
        )
        intermediates.append(current) if current != audio_path else None
        current = tmp
        stats.silences_removed_count = len(silences)
        stats.silences_removed_seconds = sum(
            max(0.0, (e - s) - keep_silence) for s, e in silences
        )

    # 2. Speed
    if abs(speed - 1.0) > 0.001:
        tmp = audio_path + ".speed.mp3"
        _change_speed(current, tmp, speed, binaries_dir)
        if current != audio_path:
            intermediates.append(current)
        current = tmp

    # 3. Swap: salva original como .raw.mp3 e promove o processado
    if current != audio_path:
        raw_backup = os.path.splitext(audio_path)[0] + ".raw.mp3"
        if os.path.exists(raw_backup):
            try:
                os.remove(raw_backup)
            except OSError:
                pass
        shutil.move(audio_path, raw_backup)
        shutil.move(current, audio_path)
        for tf in intermediates:
            if tf != audio_path and os.path.isfile(tf):
                try:
                    os.remove(tf)
                except OSError:
                    pass

    # 4. Alignment — estimativa rough + calibração linear para bater com o mp3 real
    new_alignment = _adjust_alignment(alignment, silences, keep_silence, speed)
    real_dur = _ffprobe_duration(audio_path, binaries_dir)
    if real_dur > 0 and new_alignment.duration > 0:
        scale = real_dur / new_alignment.duration
        # Se a diferença for significativa, recalibra para garantir que a última
        # char termine exatamente na duração real do arquivo (evita drift de
        # legenda na Fase 6).
        if abs(scale - 1.0) > 0.001:
            new_alignment = Alignment(
                characters=new_alignment.characters,
                starts=[t * scale for t in new_alignment.starts],
                ends=[t * scale for t in new_alignment.ends],
            )
    stats.final_duration = real_dur or new_alignment.duration
    return new_alignment, stats
