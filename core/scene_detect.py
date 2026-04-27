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
    """Decompõe um beat em N sub-clipes equilibrados, 1 por cena.

    Filosofia: melhor 3 sub-clipes de 2.3s cada do que 2 de 2s + 1 de 1s
    (que vira flash). Distribui beat_duration uniformemente entre as cenas
    viáveis adjacentes em vez de pegar `target` rígido em cada uma.

    Algoritmo:
    1. Beat curto (≤ target) e cena cabe inteira (com snap pra trás): single-cut.
    2. Caso geral:
       - Coleta cenas viáveis na janela (size ≥ NON_FLASH = 0.8s)
       - N = round(beat_duration / target), capped por len(viable)
       - Reduz N enquanto beat_duration/N < NON_FLASH (evita flash)
       - Distribui beat_duration / N por cena, capped pelo tamanho da cena
       - Sobra de cenas curtas flui pra próximas
       - Se ainda sobra: estende última (último recurso)

    `avoid_scene_start`: cena onde o BEAT anterior terminou. Pula ela pra
    evitar repetição visual entre beats.

    Retorna lista de (start, duration). Soma ≈ beat_duration.
    """
    GHOST_GUARD = 0.15
    SAFETY = 0.05       # margem pra não encostar em scene boundary
    NON_FLASH = 0.8     # sub-clipe < 0.8s vira flash visível

    if beat_duration <= 0:
        return [(cue_start, beat_duration)]

    # === ESTRATÉGIA 1: beat até 2x target → tenta single-cut ===
    # Threshold ampliado pra cobrir beats médios (até 4s default). Snap pra
    # trás é seguro DESDE QUE cue_start esteja a ≥ 0.5s do próximo scene
    # change — evita pegar conteúdo da cena ANTERIOR quando o matcher snappou
    # pro fim de uma cena (caso "Qifrey explica" antes de "Coco pratica").
    base_threshold = target_subclip_duration * 2.0
    MIN_GAP_TO_NEXT_SCENE = 0.5
    if scenes and beat_duration <= base_threshold:
        cur_scene_start = max(
            (s for s in scenes if s <= cue_start), default=0.0
        )
        next_scene_after = next(
            (s for s in scenes if s > cue_start), float("inf")
        )
        cena_room_from_cue = next_scene_after - cue_start
        cena_total = next_scene_after - cur_scene_start
        if cena_room_from_cue >= beat_duration + SAFETY:
            # Cabe sem snappar pra trás. Se cue_start cai EXATAMENTE numa
            # scene change (matcher snappou), adiciona ghost guard pra não
            # pegar 1 frame da cena anterior.
            near_scene = next(
                (s for s in scenes if abs(s - cue_start) < 0.05), None
            )
            if (
                near_scene is not None
                and cena_room_from_cue >= beat_duration + GHOST_GUARD + SAFETY
            ):
                return [(cue_start + GHOST_GUARD, beat_duration)]
            return [(cue_start, beat_duration)]
        if (
            cena_total >= beat_duration + GHOST_GUARD + SAFETY
            and cena_room_from_cue >= MIN_GAP_TO_NEXT_SCENE
        ):
            # Snappa pra trás dentro da MESMA cena. Só se cue_start está
            # a ≥ 0.5s do próximo scene change — senão a "cena atual" do
            # algoritmo é a cena ANTERIOR (cue está no fim dela), e snap
            # pra trás mostraria conteúdo narrativamente errado.
            deficit = (beat_duration + SAFETY) - cena_room_from_cue
            min_floor = cur_scene_start + GHOST_GUARD
            new_start = max(min_floor, cue_start - deficit)
            return [(new_start, beat_duration)]

    # === ESTRATÉGIA 2: distribuição equilibrada entre N cenas ===
    # Janela de contexto: estende até a próxima scene change após o
    # `desired_end` pra que a última cena viável termine numa boundary
    # natural (sem flash), com cap pra não pegar cenas longe do cue
    # (fora do contexto narrativo).
    win_start = max(0.0, cue_start)
    desired_end = win_start + beat_duration + SAFETY
    next_sc_after_desired = next(
        (s for s in scenes if s > desired_end), float("inf")
    )
    # Cap absoluto: 2x beat_duration + 1s — limita quanto a janela pode
    # estender pra evitar incluir cenas narrativamente fora do contexto.
    max_win_end = win_start + beat_duration * 2.0 + 1.0
    win_end = min(next_sc_after_desired, max_win_end)
    if win_end < desired_end:
        win_end = desired_end

    scene_set = set(scenes)
    boundaries = sorted(set(
        [win_start] + [s for s in scenes if win_start < s < win_end]
    ))

    # Coleta cenas viáveis: (start_after_ghost_guard, max_size)
    viable: List[tuple] = []
    for i, b in enumerate(boundaries):
        actual = b + GHOST_GUARD if b in scene_set else b
        nxt = boundaries[i + 1] if i + 1 < len(boundaries) else win_end
        size = nxt - actual - SAFETY
        if size >= NON_FLASH:
            viable.append((actual, size))

    # Pula primeira cena se for a mesma onde o beat anterior terminou
    if avoid_scene_start is not None and viable:
        first_actual = viable[0][0]
        first_scene_start = first_actual - GHOST_GUARD if first_actual > 0 else first_actual
        if abs(first_scene_start - avoid_scene_start) < 0.5 and len(viable) > 1:
            viable = viable[1:]

    if not viable:
        return [(cue_start, beat_duration)]

    # Decide N (número de sub-clipes / cenas a usar)
    n_ideal = max(1, round(beat_duration / target_subclip_duration))
    n = min(n_ideal, len(viable))

    # Garante que as N cenas selecionadas COBREM o beat. Se a soma do tamanho
    # delas é menor que beat_duration, sobra carry no final que estoura a
    # última boundary (= flash). Inclui mais cenas até cobrir.
    while n < len(viable) and sum(v[1] for v in viable[:n]) < beat_duration + SAFETY:
        n += 1

    # Não deixa fatia média virar flash. Reduz N enquanto avg < piso, MAS
    # NÃO reduz abaixo do necessário pra cobertura (preferimos sub-clipe curto
    # a estouro de boundary).
    min_n_for_coverage = 1
    cumsum = 0.0
    for i, (_, sz) in enumerate(viable[:n]):
        cumsum += sz
        if cumsum >= beat_duration + SAFETY:
            min_n_for_coverage = i + 1
            break
    else:
        min_n_for_coverage = n  # se não cobre, mantém o que tem

    while n > max(1, min_n_for_coverage) and (beat_duration / n) < NON_FLASH:
        n -= 1

    avg = beat_duration / n
    selected = viable[:n]

    # Distribui: cada cena pega `avg + sobra`, capped pelo tamanho.
    # Sobra (quando cena é menor que avg) flui pra próxima.
    subclips: List[tuple] = []
    carry = 0.0
    for i, (cstart, csize) in enumerate(selected):
        wanted = avg + carry
        take = min(wanted, csize)
        carry = wanted - take
        subclips.append((cstart, take))

    # Se ainda sobrou tempo (cenas pequenas demais), tenta usar próxima cena
    # viável fora das selecionadas.
    if carry > SAFETY and len(viable) > n:
        for extra_start, extra_size in viable[n:]:
            if carry <= SAFETY:
                break
            take = min(carry, extra_size)
            if take >= NON_FLASH:
                subclips.append((extra_start, take))
                carry -= take

    # Último recurso: estende o último sub-clipe (pode estourar boundary,
    # mas a soma das durações precisa = beat_duration pra sync com áudio).
    if carry > SAFETY and subclips:
        ls, ld = subclips[-1]
        subclips[-1] = (ls, ld + carry)

    if not subclips:
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
