"""Quebra a narração em micro-beats de ~2-3 segundos.

Usa o alignment a nível de caractere do ElevenLabs pra achar pontos de quebra
naturais (pontuação) e forçar quebras quando o beat fica longo demais.
"""
from dataclasses import dataclass
from typing import List

from core.tts import Alignment

# Prioridade de quebra: ponto final > vírgula/ponto-e-vírgula > traço/reticências.
_HARD_PUNCT = set(".!?")
_SOFT_PUNCT = set(",;:—…")


@dataclass
class NarrationBeat:
    """Unidade temporal — um pedaço da narração com texto + tempos."""
    text: str
    start: float
    end: float
    index: int = 0

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    def label(self) -> str:
        return f"[{self.index:02d}] {self.start:5.2f}-{self.end:5.2f}s  \"{self.text}\""


def chunk_by_time(
    alignment: Alignment,
    target_seconds: float = 2.5,
    max_seconds: float = 3.5,
) -> List[NarrationBeat]:
    """Varre caracteres acumulando um beat. Quebra quando:
    - elapsed ≥ target_seconds E caractere é pontuação forte (. ! ?)
    - elapsed ≥ target_seconds E caractere é pontuação suave (, ; : — …)
    - elapsed ≥ max_seconds — força corte na última quebra de palavra disponível

    Sempre fecha no char que disparou a quebra, pra manter pontuação visualmente.
    """
    chars = alignment.characters
    starts = alignment.starts
    ends = alignment.ends
    if not chars or len(chars) != len(starts) or len(chars) != len(ends):
        return []

    beats: List[NarrationBeat] = []
    block_start_idx = 0
    block_start_time = starts[0]

    def _emit(end_idx: int, end_time: float):
        text = ''.join(chars[block_start_idx:end_idx + 1]).strip()
        if text:
            beats.append(NarrationBeat(
                text=text,
                start=block_start_time,
                end=end_time,
                index=len(beats) + 1,
            ))

    last_space_idx = None  # última quebra de palavra vista (pra corte forçado)

    for i, c in enumerate(chars):
        if c == ' ':
            last_space_idx = i

        elapsed = ends[i] - block_start_time
        should_break = False
        break_idx = i
        break_time = ends[i]

        if elapsed >= target_seconds and c in _HARD_PUNCT:
            should_break = True
        elif elapsed >= target_seconds and c in _SOFT_PUNCT:
            should_break = True
        elif elapsed >= max_seconds:
            # força corte; prefere quebrar numa palavra recente
            if last_space_idx is not None and last_space_idx > block_start_idx:
                break_idx = last_space_idx - 1  # até antes do espaço
                break_time = ends[break_idx] if break_idx >= 0 else ends[i]
            should_break = True

        if should_break:
            _emit(break_idx, break_time)
            if break_idx + 1 < len(starts):
                block_start_idx = break_idx + 1
                block_start_time = starts[break_idx + 1]
                last_space_idx = None
            else:
                block_start_idx = len(chars)
                break

    # Flush do resto
    if block_start_idx < len(chars):
        _emit(len(chars) - 1, ends[-1])

    return beats
