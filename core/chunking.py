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

# Conjunções coordenativas: vírgula + uma dessas indica nova clausula com
# sujeito/ação distinta ("Qifrey aparece, E a Tetia quebra o selo").
# Tratada como HARD (quebra após target, sem esperar soft_threshold).
_COORDINATING_CONJUNCTIONS = (" e ", " mas ", " ou ", " porém ", " contudo ", " entretanto ", " todavia ")


def _is_coordinating_after(chars, i: int) -> bool:
    """True se logo após chars[i] (uma vírgula) vem uma conjunção coordenativa."""
    # Olha os próximos 12 chars (cobre " entretanto " que é a maior)
    rest = ''.join(chars[i + 1:i + 13]).lower()
    return any(rest.startswith(conj) for conj in _COORDINATING_CONJUNCTIONS)


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
    min_hard_break: float = 1.0,
) -> List[NarrationBeat]:
    """Quebra priorizando pontuação FORTE (fim de sentença/preâmbulo) sobre
    FRACA (pausas) pra evitar cortes no meio da ideia.

    Hierarquia:
    - Elapsed < min_hard_break            → nunca quebra (evita beat <1s)
    - min_hard_break ≤ elapsed < target   → quebra SÓ em HARD (. ! ? :)
    - target ≤ elapsed < soft_threshold   → idem (HARD)
    - virgula + conjunção coordenativa     → quebra (clausula coordenada)
    - soft_threshold ≤ elapsed < max      → aceita também SOFT (, ; — …)
    - elapsed ≥ max_seconds               → retrocede pra última pontuação

    Com isso "Takuya pede que, se ele ganhar, ela o abrace." vira 1 beat só
    (ignora vírgulas internas, espera o ponto final). Mas "mas Qifrey salva
    a Agott. E é nesse momento que..." quebra logo em "Agott." (1.14s ≥ 1.0)
    pra deixar cada frase em seu beat.
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
    last_punct_idx = None  # última pontuação SOFT/HARD vista
    last_punct_time = None

    for i, c in enumerate(chars):
        if c == ' ':
            last_space_idx = i
        if c in _HARD_PUNCT or c in _SOFT_PUNCT:
            last_punct_idx = i
            last_punct_time = ends[i]

        elapsed = ends[i] - block_start_time
        should_break = False
        break_idx = i
        break_time = ends[i]

        if elapsed >= min_hard_break and c in _HARD_PUNCT:
            should_break = True
        elif (
            elapsed >= target_seconds
            and c == ','
            and _is_coordinating_after(chars, i)
        ):
            # Vírgula + conjunção coordenativa = mudança de cláusula
            # ("X, e Y" = duas ações). Quebra mesmo sem soft_threshold.
            should_break = True
        elif elapsed >= soft_threshold and c in _SOFT_PUNCT:
            should_break = True
        elif elapsed >= max_seconds:
            # Atingiu o limite duro. Em vez de cortar mecânico no espaço
            # (no meio de uma ideia tipo "...visão bizarra | da Bruxa..."),
            # PREFERE retroceder pra última pontuação SOFT/HARD já vista —
            # se ela criou um beat com elapsed >= target_seconds. Beat fica
            # mais curto mas semanticamente íntegro.
            if (
                last_punct_idx is not None
                and last_punct_idx > block_start_idx
                and (last_punct_time - block_start_time) >= target_seconds
            ):
                break_idx = last_punct_idx
                break_time = last_punct_time
            elif last_space_idx is not None and last_space_idx > block_start_idx:
                # Sem pontuação utilizável: fallback pra última palavra
                break_idx = last_space_idx - 1
                break_time = ends[break_idx] if break_idx >= 0 else ends[i]
            should_break = True

        if should_break:
            _emit(break_idx, break_time)
            if break_idx + 1 < len(starts):
                block_start_idx = break_idx + 1
                block_start_time = starts[break_idx + 1]
                last_space_idx = None
                last_punct_idx = None
                last_punct_time = None
            else:
                block_start_idx = len(chars)
                break

    # Flush do resto
    if block_start_idx < len(chars):
        _emit(len(chars) - 1, ends[-1])

    return beats
