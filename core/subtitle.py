"""Carrega .srt/.ass e devolve cues com timestamps + texto limpo agregado.

O texto limpo reproduz exatamente o comportamento do app 1.2.4:
- em .ass, remove tags `{...}` e linhas com estilos de música/karaokê
- em .srt, remove tags HTML e linhas com ♪/♫
- uma cue por linha no texto final
"""
import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

from core.cue import Cue

_SONG_STYLES = ('op', 'ed', 'song', 'romaji', 'karaoke', 'opening', 'ending')
_TAG_RE = re.compile(r'\{.*?\}')
_HTML_RE = re.compile(r'<[^>]+>')
_ARROW = '-->'

# Cue "descritiva" de CC: texto totalmente entre [] ou (), tipo "[footsteps]"
# ou "(ominous music)". Útil pro matcher (ancora momentos sem diálogo) mas
# NÃO conta pra detectar gap OP/ED — senão nunca acha o gap com CC.
_DESCRIPTOR_RE = re.compile(r'^\s*[\[\(].*[\]\)]\s*$', re.DOTALL)


def is_descriptor_cue(text: str) -> bool:
    """True se a cue é descrição de cena/som (ex: '[footsteps approaching]',
    '(ominous music)', '[Coco gasps]') em vez de diálogo falado."""
    if not text:
        return True
    return bool(_DESCRIPTOR_RE.match(text.strip()))


@dataclass
class CleanedSubtitle:
    cues: List[Cue]
    plain_text: str


def _ass_ts_to_seconds(ts: str) -> float:
    parts = ts.strip().split(':')
    if len(parts) != 3:
        return 0.0
    try:
        h = int(parts[0])
        m = int(parts[1])
        rest = parts[2]
        if '.' in rest:
            s_str, cs_str = rest.split('.', 1)
        else:
            s_str, cs_str = rest, '0'
        s = int(s_str)
        cs = int(cs_str.ljust(2, '0')[:2])
        return h * 3600 + m * 60 + s + cs / 100.0
    except ValueError:
        return 0.0


_SRT_TS_RE = re.compile(r'(\d+):(\d+):(\d+)[,.](\d+)')


def _srt_ts_to_seconds(ts: str) -> float:
    m = _SRT_TS_RE.search(ts)
    if not m:
        return 0.0
    h, mi, s, ms = m.groups()
    return int(h) * 3600 + int(mi) * 60 + int(s) + int(ms) / 1000.0


def parse_ass(lines) -> List[Cue]:
    cues: List[Cue] = []
    for line in lines:
        if not line.startswith("Dialogue:"):
            continue
        parts = line.split(',', 9)
        if len(parts) <= 9:
            continue
        style = parts[3].lower()
        text = parts[9].strip()
        if any(s in style for s in _SONG_STYLES) or '♪' in text or '♫' in text:
            continue
        text = _TAG_RE.sub('', text)
        text = text.replace('\\N', ' ').replace('\\n', ' ')
        if not text:
            continue
        start = _ass_ts_to_seconds(parts[1])
        end = _ass_ts_to_seconds(parts[2])
        # Campo `Name` (parts[4]): nem todo ripper preenche. Quando preenche,
        # é o speaker da linha — ouro pra evitar atribuição errada de falas
        # no resumo. Limpa whitespace e descarta valores genéricos.
        speaker = parts[4].strip()
        if speaker.lower() in ("", "default", "speaker", "narrator", "?"):
            speaker = ""
        cues.append(Cue(start=start, end=end, text=text, speaker=speaker))
    return cues


def parse_srt(lines) -> List[Cue]:
    cues: List[Cue] = []
    block_text: List[str] = []
    start = end = 0.0
    for raw in lines:
        line = raw.strip()
        if re.match(r'^\d+$', line):
            continue
        if _ARROW in line:
            left, _, right = line.partition(_ARROW)
            start = _srt_ts_to_seconds(left)
            end = _srt_ts_to_seconds(right)
            continue
        if line == '':
            if block_text:
                joined = ' '.join(block_text)
                if '♪' not in joined and '♫' not in joined:
                    cues.append(Cue(start=start, end=end, text=joined))
                block_text = []
            continue
        block_text.append(_HTML_RE.sub('', line))
    return cues


def _format_cue_for_transcript(c: Cue) -> str:
    """Formata uma cue pro transcript que vai ao LLM resumidor.

    Quando há speaker do .ass Name, prefixa "(NOME) " — ajuda o LLM a
    atribuir falas corretamente em cenas com múltiplos personagens.
    Sem speaker, só o texto.
    """
    if c.speaker:
        return f"({c.speaker}) {c.text}"
    return c.text


def load_subtitle(file_path: str) -> CleanedSubtitle:
    with open(file_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    if file_path.lower().endswith('.ass'):
        cues = parse_ass(lines)
    else:
        cues = parse_srt(lines)
    # Inclui speaker quando o .ass preencheu o Name — fundamental pra resumo
    # acurado em cenas multi-personagem.
    plain = '\n'.join(_format_cue_for_transcript(c) for c in cues)
    return CleanedSubtitle(cues=cues, plain_text=plain)


def detect_music_gaps(
    cues: List[Cue],
    mkv_duration: float = 0.0,
    min_gap: float = 45.0,
    min_end_gap: float = 25.0,
    pad_before: float = 5.0,
    pad_after: float = 5.0,
    scene_changes: Optional[List[float]] = None,
) -> List[Tuple[float, float]]:
    """Detecta regiões de OP/ED/música analisando gaps no diálogo.

    OP normalmente tem ~90s SEM subtítulo (só instrumental + karaoke raro).
    ED similar (~60-90s). Gap > min_gap entre cues adjacentes = candidato.

    Cues descritivas (CC tipo '[music]', '(theme plays)') são IGNORADAS
    pra contagem de gap — senão OP nunca é detectada em CC tracks.

    Aplica padding (`pad_before` antes do gap, `pad_after` depois) pra
    englobar title cards e previews adjacentes que têm dialog "Default"
    mas visualmente pertencem à borda da OP/ED.

    Se `scene_changes` é fornecido, o padding NÃO ultrapassa uma scene
    change adjacente — evita engolir cenas silenciosas mas legítimas
    que ficam logo antes da OP/ED (ex: thought bubble final do ep,
    transição visual antes dos créditos).

    Requer `mkv_duration` pra detectar o gap final. Se 0, ignora.
    """
    if not cues:
        return []

    # Filtra descritivas — elas existem durante OP em CC tracks e quebrariam
    # a detecção. Gap detectado é baseado em DIÁLOGO real.
    dialogue_cues = [c for c in cues if not is_descriptor_cue(c.text)]
    if not dialogue_cues:
        return []

    cues_sorted = sorted(dialogue_cues, key=lambda c: c.start)
    regions: List[Tuple[float, float]] = []

    # Gap inicial (0s → primeira cue)
    first = cues_sorted[0]
    if first.start >= min_gap:
        regions.append((0.0, first.start))

    # Gaps entre cues consecutivas
    for i in range(len(cues_sorted) - 1):
        end_prev = cues_sorted[i].end
        start_next = cues_sorted[i + 1].start
        gap = start_next - end_prev
        if gap >= min_gap:
            regions.append((end_prev, start_next))

    # Gap final (última cue → fim do mkv)
    if mkv_duration > 0:
        last_end = cues_sorted[-1].end
        if mkv_duration - last_end >= min_end_gap:
            regions.append((last_end, mkv_duration))

    # Aplica padding: expande cada região pra fora.
    # Se temos scene_changes, o pad não ultrapassa scene change adjacente —
    # evita engolir cenas silenciosas legítimas (thought bubble, transição
    # visual) que ficam entre a última fala e o início real da OP/ED.
    if pad_before > 0 or pad_after > 0:
        scenes = sorted(scene_changes or [])
        new_regions: List[Tuple[float, float]] = []
        for a, b in regions:
            # pad_before: expande pra trás, mas para na scene change mais
            # recente dentro do range (a - pad_before, a).
            new_a = max(0.0, a - pad_before)
            if scenes:
                blocking = [
                    s for s in scenes
                    if new_a < s < a
                ]
                if blocking:
                    new_a = max(blocking)  # scene change mais próxima de `a`
            # pad_after: idem na frente.
            new_b = b + pad_after
            if scenes:
                blocking = [
                    s for s in scenes
                    if b < s < new_b
                ]
                if blocking:
                    new_b = min(blocking)  # scene change mais próxima de `b`
            new_regions.append((new_a, new_b))
        regions = new_regions

    return regions


def detect_op_ed_regions_by_style(file_path: str) -> List[Tuple[float, float]]:
    """Detecta OP/ED via estilo das cues no .ass. Nem todo anime marca;
    quando não marca, usa detect_music_gaps como fallback no caller.
    """
    if not file_path.lower().endswith('.ass'):
        return []

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    except Exception:
        return []

    song_times: List[Tuple[float, float]] = []
    for line in lines:
        if not line.startswith("Dialogue:"):
            continue
        parts = line.split(',', 9)
        if len(parts) <= 9:
            continue
        style = parts[3].lower()
        text = parts[9].strip()
        is_song_style = any(s in style for s in _SONG_STYLES)
        has_song_symbol = '♪' in text or '♫' in text
        if not (is_song_style or has_song_symbol):
            continue
        try:
            start = _ass_ts_to_seconds(parts[1])
            end = _ass_ts_to_seconds(parts[2])
            if end > start:
                song_times.append((start, end))
        except Exception:
            pass

    if not song_times:
        return []

    song_times.sort()
    regions: List[Tuple[float, float]] = []
    cur_start, cur_end = song_times[0]
    for s, e in song_times[1:]:
        if s - cur_end < 10.0:
            cur_end = max(cur_end, e)
        else:
            regions.append((cur_start, cur_end))
            cur_start, cur_end = s, e
    regions.append((cur_start, cur_end))
    # Só retorna se as regiões são longas (>30s) — evita falsos positivos
    # de on-screen text que também usa estilo "Song"
    return [(a, b) for a, b in regions if b - a > 30.0]


def filter_cues_outside_regions(cues: List[Cue], regions: List[Tuple[float, float]]) -> List[Cue]:
    """Devolve as cues cujo start NÃO cai dentro de nenhuma região bloqueada."""
    if not regions:
        return cues
    def _in_region(t: float) -> bool:
        return any(a <= t <= b for a, b in regions)
    return [c for c in cues if not _in_region(c.start)]


def load_cues_for_matcher(file_path: str, exclude_signs: bool = True) -> List[Cue]:
    """Versão de load_subtitle pro matcher: opcionalmente filtra cues de
    style 'Signs' (placas/títulos na tela) que não representam diálogo de
    personagem e causam matches ruins (ex: title cards após OP).

    Só funciona em .ass; .srt é retornado sem filtro.
    """
    if not file_path.lower().endswith('.ass') or not exclude_signs:
        return load_subtitle(file_path).cues

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    except Exception:
        return load_subtitle(file_path).cues

    cues: List[Cue] = []
    for line in lines:
        if not line.startswith("Dialogue:"):
            continue
        parts = line.split(',', 9)
        if len(parts) <= 9:
            continue
        style = parts[3].lower()
        text = parts[9].strip()

        # Pula música/OP/ED (já era antes)
        if any(s in style for s in _SONG_STYLES) or '♪' in text or '♫' in text:
            continue
        # Pula texto on-screen (placas, títulos de episódio)
        if 'sign' in style or 'title' in style or 'logo' in style:
            continue

        text = _TAG_RE.sub('', text)
        text = text.replace('\\N', ' ').replace('\\n', ' ')
        if not text:
            continue
        try:
            start = _ass_ts_to_seconds(parts[1])
            end = _ass_ts_to_seconds(parts[2])
            speaker = parts[4].strip()
            if speaker.lower() in ("", "default", "speaker", "narrator", "?"):
                speaker = ""
            cues.append(Cue(start=start, end=end, text=text, speaker=speaker))
        except Exception:
            continue
    return cues
