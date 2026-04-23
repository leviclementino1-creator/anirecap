"""Quebra a narração em micro-beats de ~2-3 segundos.

Usa o alignment a nível de caractere do ElevenLabs pra achar pontos de quebra
naturais (pontuação) e forçar quebras quando o beat fica longo demais.
"""
from dataclasses import dataclass
from typing import List

from core.tts import Alignment

# Prioridade de quebra:
# - FORTE: fim de sentença ou preâmbulo de fala ("Ela disse:").
# - FRACA: pausas curtas, só quebram se já estivermos perto do limite.
_HARD_PUNCT = set(".!?:")
_SOFT_PUNCT = set(",;—…")


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
    target_seconds: float = 2.0,
    soft_threshold: float = 3.0,
    max_seconds: float = 4.5,
) -> List[NarrationBeat]:
    """Quebra priorizando pontuação FORTE (fim de sentença/preâmbulo) sobre
    FRACA (pausas) pra evitar cortes no meio da ideia.

    Hierarquia:
    - Elapsed < target_seconds          → nunca quebra
    - target ≤ elapsed < soft_threshold → quebra SÓ em HARD (. ! ? :)
    - soft_threshold ≤ elapsed < max    → aceita também SOFT (, ; — …)
    - elapsed ≥ max_seconds             → força quebra no último espaço

    Com isso "Takuya pede que, se ele ganhar, ela o abrace." vira 1 beat só,
    porque ignora as vírgulas do meio e espera o ponto final.
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

    last_space_idx = None  # última quebra de palavra (pra corte forçado)

    for i, c in enumerate(chars):
        if c == ' ':
            last_space_idx = i

        elapsed = ends[i] - block_start_time
        should_break = False
        break_idx = i
        break_time = ends[i]

        if elapsed >= target_seconds and c in _HARD_PUNCT:
            should_break = True
        elif elapsed >= soft_threshold and c in _SOFT_PUNCT:
            should_break = True
        elif elapsed >= max_seconds:
            # Último recurso: força corte na última palavra antes do limite
            if last_space_idx is not None and last_space_idx > block_start_idx:
                break_idx = last_space_idx - 1
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
