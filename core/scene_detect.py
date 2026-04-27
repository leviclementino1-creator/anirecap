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
from typing import List, Optional

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


def find_clean_window(
    cue_start: float,
    cue_end: float,
    beat_duration: float,
    scenes: List[float],
    proximity: float = 6.0,
    min_clip_duration: float = 0.8,
    edge_min_duration: float = 1.0,
) -> float:
    """Escolhe video_start próximo do cue.start que minimiza FLASHES no beat.

    Animes têm scene changes a cada 0.5-1.5s em sequências de ação, então
    cortes rápidos NO MEIO de um beat são naturais e não viram bug visual.
    O que incomoda é flash NA BORDA (início/fim): a cena começa, vê 0.2s
    dela, troca pra outra. Esse algoritmo distingue:

    - Flash crítico (sub-clip < min_clip_duration): -200 nas bordas, -20 no meio
    - Sub-clip < edge_min_duration nas bordas: -100 (ainda incômodo)
    - Sub-clips >= edge_min_duration: bonus por tempo limpo

    Algoritmo:
    - Candidatos: scene changes em [cue_start - proximity, cue_start + proximity]
                  + cue_start original (fallback)
    - Score: ver acima, tie-breaker por proximidade do cue_start
    """
    if not scenes or beat_duration <= 0:
        return cue_start

    candidates = [cue_start]
    for s in scenes:
        if s < cue_start - proximity:
            continue
        if s > cue_start + proximity:
            break
        candidates.append(s)

    best_start = cue_start
    best_score = -float("inf")

    for start in candidates:
        end = start + beat_duration
        cuts_inside = [s for s in scenes if start < s < end]
        boundaries = [start] + cuts_inside + [end]
        sub_durations = [
            boundaries[i + 1] - boundaries[i]
            for i in range(len(boundaries) - 1)
        ]

        if not sub_durations:
            continue

        score = 0.0
        last_idx = len(sub_durations) - 1

        for i, d in enumerate(sub_durations):
            is_edge = (i == 0 or i == last_idx)

            if d < min_clip_duration:
                # Flash crítico (< 0.8s)
                score -= 200 if is_edge else 20
            elif is_edge and d < edge_min_duration:
                # Borda curta mas não flash (entre 0.8 e 1.0s)
                score -= 100
            else:
                # Sub-clip OK
                score += d

        # Tie-breaker: penalty leve por distância do cue_start
        score -= abs(start - cue_start) * 0.3

        if score > best_score:
            best_score = score
            best_start = start

    return best_start


def pick_subclips(
    cue_start: float,
    cue_end: float,
    beat_duration: float,
    scenes: List[float],
    target_subclip_duration: float = 2.0,
    context_pad_after: float = 3.0,
    min_subclip: float = 1.0,
    avoid_scene_start: Optional[float] = None,
) -> List[tuple]:
    """Decompõe um beat em múltiplos sub-clipes pra ritmo de short.

    Em vez de cortar 1 clipe contínuo de `beat_duration` segundos da janela
    de contexto, divide em N sub-clipes de ~target_subclip_duration cada,
    respeitando scene changes naturais como pontos de corte.

    Comportamento:
    - Se a janela tem scene changes: usa eles como boundaries naturais.
    - Se uma cena é mais longa que target (ex: 6s sem cuts): split artificial,
      pulando 0.3s entre sub-clipes pra criar movimento.
    - Sub-clipe mínimo: 1.0s (sem flashes).
    - Skip de 0.15s logo após cada scene change (evita ghost frame).
    - Sobra de tempo é distribuída SOMENTE dentro dos sub-clipes existentes
      (sem estourar scene boundaries — evita flash final).

    `avoid_scene_start`: timestamp do início da cena onde o BEAT ANTERIOR
    terminou. Se o primeiro candidato deste beat cair na mesma cena, pula
    pra próxima cena pra evitar repetição visual entre beats.

    Retorna lista de (start, duration). Soma das durations = beat_duration.
    Se nada funciona (cue muito curta, sem scenes adjacentes), fallback pra
    1 sub-clipe contínuo de cue_start a cue_start+beat_duration.
    """
    GHOST_GUARD = 0.15
    EXTRA_PAUSE = 0.3   # gap entre sub-clipes da mesma cena (movimento artificial)
    SAFETY = 0.05       # margem pra não encostar em scene boundary ao estender

    if beat_duration <= 0:
        return [(cue_start, beat_duration)]

    win_start = max(0.0, cue_start)
    win_end = cue_end + context_pad_after
    # Garante que a janela cobre pelo menos beat_duration + folga
    if win_end < win_start + beat_duration + 2.0:
        win_end = win_start + beat_duration + 2.0

    # Boundaries pra varrer: começo da janela + scene changes dentro dela
    scene_set = set(scenes)
    boundaries = sorted(set(
        [win_start] + [s for s in scenes if win_start < s < win_end]
    ))

    # Se o beat anterior terminou numa cena cujo início bate com o primeiro
    # boundary deste beat, pula essa cena pra evitar repetição visual.
    if avoid_scene_start is not None and boundaries:
        first_b = boundaries[0]
        # `avoid_scene_start` é o início da cena anterior; se for igual ou
        # bem próximo de `first_b`, são a mesma cena — descarta.
        if abs(first_b - avoid_scene_start) < 0.5 and len(boundaries) > 1:
            boundaries = boundaries[1:]

    subclips: List[tuple] = []
    total_needed = beat_duration

    for i, b_start in enumerate(boundaries):
        if total_needed < 0.3:
            break

        # Skip de ghost guard SE essa boundary é uma scene change real
        # (não a borda inicial da janela)
        if b_start in scene_set:
            actual_start = b_start + GHOST_GUARD
        else:
            actual_start = b_start

        next_b = boundaries[i + 1] if i + 1 < len(boundaries) else win_end
        cena_remaining = next_b - actual_start

        if cena_remaining < min_subclip:
            continue  # cena curta = flash, pula

        # Dentro desta cena, cria sub-clipes pulando target+pause cada
        cur = actual_start
        while cur + min_subclip <= next_b and total_needed >= min_subclip:
            max_take = min(target_subclip_duration, next_b - cur, total_needed)
            if max_take < min_subclip:
                break
            subclips.append((cur, max_take))
            total_needed -= max_take
            cur += max_take + EXTRA_PAUSE

        if total_needed < min_subclip:
            break

    # Distribui o tempo restante entre sub-clipes existentes, do último pro
    # primeiro, SEM estourar scene boundaries nem o início do próximo sub-clipe.
    # Isso evita o "flash final" antigo (sub-clipe esticava pra além da cena).
    if total_needed > 0 and subclips:
        for i in range(len(subclips) - 1, -1, -1):
            if total_needed <= SAFETY:
                break
            sub_s, sub_d = subclips[i]
            sub_end = sub_s + sub_d
            # Cap superior: próxima scene boundary OU início do próximo sub-clipe
            next_scene = next((s for s in scenes if s > sub_end), float("inf"))
            next_sub_start = (
                subclips[i + 1][0] if i + 1 < len(subclips) else float("inf")
            )
            cap = min(next_scene, next_sub_start) - SAFETY
            room = max(0.0, cap - sub_end)
            take = min(room, total_needed)
            if take > 0:
                subclips[i] = (sub_s, sub_d + take)
                total_needed -= take

        # Se MESMO ASSIM sobrou tempo (scenes muito juntas), estende o último
        # mesmo passando da boundary — flash curto é melhor que dessincronia
        # áudio/vídeo. Caso raro.
        if total_needed > SAFETY:
            last_s, last_d = subclips[-1]
            subclips[-1] = (last_s, last_d + total_needed)

    if not subclips:
        # Fallback: nenhum sub-clipe válido (cue curtíssima sem scenes próximas)
        return [(cue_start, beat_duration)]

    return subclips


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
