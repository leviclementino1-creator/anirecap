"""Gera `.ass` com captions word-by-word a partir do alignment do ElevenLabs.

Cada palavra aparece exatamente no momento que é falada e some quando a
próxima começa (nunca fica vazio durante uma respiração curta — evita pisca).

Estilo alvo: Arial Black branco com outline preto grosso, centro-inferior
por volta de 78% da altura. Padrão de short TikTok/Reels.
"""
from typing import List, Tuple

from core.tts import Alignment


ASS_HEADER = """[Script Info]
Title: Ancopy captions
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial Black,{fontsize},&H00FFFFFF,&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,{outline},0,2,60,60,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _time_to_ass(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    cs = int(round((seconds - int(seconds)) * 100))
    s = int(seconds) % 60
    if cs >= 100:
        cs = 99
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _alignment_to_words(alignment: Alignment) -> List[Tuple[str, float, float]]:
    """Varre chars acumulando palavras. Quebra em espaço, tab e newline."""
    chars = alignment.characters
    starts = alignment.starts
    ends = alignment.ends
    if not chars:
        return []

    words: List[Tuple[str, float, float]] = []
    buf: List[str] = []
    buf_start: float = None  # type: ignore

    def _flush(end_time: float):
        nonlocal buf, buf_start
        if buf and buf_start is not None:
            text = ''.join(buf).strip()
            if text:
                words.append((text, buf_start, end_time))
        buf = []
        buf_start = None

    for i, c in enumerate(chars):
        if c in (' ', '\t', '\n', '\r'):
            _flush(ends[i - 1] if i > 0 else ends[i])
        else:
            if buf_start is None:
                buf_start = starts[i]
            buf.append(c)
    _flush(ends[-1])
    return words


def _ass_escape(text: str) -> str:
    """Neutraliza caracteres ASS e normaliza. `{}` são override codes."""
    return (
        text.replace('\\', '')
            .replace('{', '(')
            .replace('}', ')')
            .replace('\n', ' ')
            .replace('\r', ' ')
            .strip()
    )


# Pontuação removida das captions (TTS preserva entonação, o visual fica limpo).
# Mantemos "?" pra marcar perguntas.
_PUNCT_TO_STRIP = set(',.!:;"“”‘’—…()[]<>*')


def _clean_caption_text(text: str) -> str:
    """Remove pontuação exceto '?' da caption word. Mantém letras, dígitos,
    acentos, apóstrofes internos (contrações raras), hífens em palavras
    compostas (via replacement) e o marcador de pergunta."""
    if not text:
        return text
    result = ''.join(ch for ch in text if ch not in _PUNCT_TO_STRIP)
    # Aspas simples comuns também
    result = result.replace("'", "").replace("`", "")
    return result.strip()


def generate_ass(
    alignment: Alignment,
    output_path: str,
    resolution: Tuple[int, int] = (1080, 1920),
    fontsize: int = 78,
    outline: int = 5,
    vertical_pct: float = 0.68,
    hold_last_extra: float = 0.4,
    uppercase: bool = True,
) -> str:
    """Gera captions word-by-word. Cada palavra dura até a próxima começar.

    - `vertical_pct=0.68` → posição vertical a 68% da altura (entre o vídeo
      central e o blur de baixo, como TikTok)
    - `fontsize=78`, `outline=5` → Arial Black branco com borda preta grossa
    - `uppercase=True` → ALL CAPS estilo viral
    """
    w, h = resolution
    margin_v = max(0, int(h * (1.0 - vertical_pct)))

    words = _alignment_to_words(alignment)

    lines = [ASS_HEADER.format(
        w=w, h=h, fontsize=fontsize, outline=outline, margin_v=margin_v,
    )]

    # Cada palavra dura até a próxima começar — evita gaps visuais durante
    # micro-pausas entre palavras. Última segura um pouco mais no ar.
    for i, (text, start, end) in enumerate(words):
        if i + 1 < len(words):
            display_end = words[i + 1][1]
        else:
            display_end = end + hold_last_extra

        safe = _ass_escape(text)
        safe = _clean_caption_text(safe)
        if not safe:
            continue
        if uppercase:
            safe = safe.upper()
        lines.append(
            f"Dialogue: 0,{_time_to_ass(start)},{_time_to_ass(display_end)},"
            f"Default,,0,0,0,,{safe}"
        )

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    return output_path
