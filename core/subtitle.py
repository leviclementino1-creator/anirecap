"""Carrega .srt/.ass e devolve cues com timestamps + texto limpo agregado.

O texto limpo reproduz exatamente o comportamento do app 1.2.4:
- em .ass, remove tags `{...}` e linhas com estilos de música/karaokê
- em .srt, remove tags HTML e linhas com ♪/♫
- uma cue por linha no texto final
"""
import re
from dataclasses import dataclass
from typing import List

from core.cue import Cue

_SONG_STYLES = ('op', 'ed', 'song', 'romaji', 'karaoke', 'opening', 'ending')
_TAG_RE = re.compile(r'\{.*?\}')
_HTML_RE = re.compile(r'<[^>]+>')
_ARROW = '-->'


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
        cues.append(Cue(start=start, end=end, text=text))
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


def load_subtitle(file_path: str) -> CleanedSubtitle:
    with open(file_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    if file_path.lower().endswith('.ass'):
        cues = parse_ass(lines)
    else:
        cues = parse_srt(lines)
    plain = '\n'.join(c.text for c in cues)
    return CleanedSubtitle(cues=cues, plain_text=plain)
