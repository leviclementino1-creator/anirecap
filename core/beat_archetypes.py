"""Classifica beats em ARQUÉTIPOS narrativos pra que o matcher use regras
diferentes em cada tipo.

INTUIÇÃO: hook, setup, escalada, payoff requerem CENAS COM PERFIS DIFERENTES.
Hook precisa do frame mais chocante. Setup pode usar wide shots. Payoff
precisa de closure visual. Sistema atual aplica as mesmas regras em tudo —
resultado: hook ocasionalmente fraco, payoff genérico.

Arquétipos:
- HOOK: 1º beat, frase de abertura (puxa o espectador)
- SETUP: explicação inicial, contexto narrativo
- ESCALADA: complicação, conflito crescente
- CLIMAX: pico dramático
- PAYOFF: closure, gancho final ("e agora...?")

Heurística: posição no roteiro + análise textual leve. Sem chamada LLM extra.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List

from core.chunking import NarrationBeat


# Marcadores textuais que indicam arquétipo
_HOOK_MARKERS = (
    "mano", "olha só", "imagina", "essa garota", "esse cara", "esse mestre",
)
_SETUP_MARKERS = (
    "tudo começou", "olha só, tudo", "começa assim", "começou com",
)
_CLIMAX_MARKERS = (
    "plot twist", "mas aí", "só que", "explode", "revela", "descobre que",
)
_PAYOFF_MARKERS = (
    "e agora", "será que", "o que vai", "o que será", "mas será", "e o que",
    "vai conseguir", "será o", "que vem por", "afinal",
)


@dataclass
class BeatArchetype:
    """Classificação de um beat com diretivas de scene selection."""
    beat_index: int
    archetype: str  # HOOK | SETUP | ESCALADA | CLIMAX | PAYOFF
    selection_hint: str  # texto curto pra injetar no prompt do matcher

    def hint_line(self) -> str:
        return f"beat {self.beat_index:02d} = {self.archetype} | {self.selection_hint}"


_SELECTION_HINTS = {
    "HOOK": (
        "PREFIRA cena de ALTO IMPACTO VISUAL — close-up emocional, ação "
        "física forte, ou momento icônico do clímax. NUNCA cena estática "
        "de prédio/paisagem. audio_energy > 0 ideal."
    ),
    "SETUP": (
        "Cena CONTEXTUAL — apresentação de personagem, ambientação. Wide "
        "shots OK. Não precisa ser spike de impacto. Diálogo + close mid OK."
    ),
    "ESCALADA": (
        "Cena de COMPLICAÇÃO ou ação crescente. Prefira cenas com ação "
        "física ou tensão visível (movimento, expressões intensas)."
    ),
    "CLIMAX": (
        "MOMENTO DE EXPLOSÃO emocional/visual. Use post_silence_pop "
        "se disponível. audio_energy ALTO. Tipicamente está no último "
        "terço temporal do ep."
    ),
    "PAYOFF": (
        "Cena de FECHAMENTO ou GANCHO. Pode ser foreshadowing visual "
        "ou último frame icônico. PREFIRA cenas no fim do ep que "
        "sugerem o próximo, ou close-up final do protagonista."
    ),
}


def _matches_any(text: str, markers: tuple) -> bool:
    text_lower = text.lower()
    return any(m in text_lower for m in markers)


def classify_beats(beats: List[NarrationBeat]) -> List[BeatArchetype]:
    """Classifica cada beat usando heurística posicional + textual.

    Algoritmo:
    1. Beat 1 (e às vezes 2): HOOK
    2. Próximo bloco com "tudo começou": SETUP, propaga 2-3 beats
    3. Marcadores de twist/explosão: CLIMAX
    4. Último beat com pergunta retórica: PAYOFF
    5. Restante: ESCALADA (default seguro)
    """
    if not beats:
        return []

    n = len(beats)
    out: List[BeatArchetype] = []

    # Identifica payoff(s) começando do fim
    payoff_indices = set()
    for i in range(n - 1, max(n - 4, 0), -1):
        if _matches_any(beats[i].text, _PAYOFF_MARKERS):
            payoff_indices.add(i)
        elif "?" in beats[i].text and i >= n - 2:
            payoff_indices.add(i)
        else:
            break  # primeiro beat sem marker = para de procurar payoff

    # Identifica hooks no início
    hook_indices = set()
    for i in range(min(3, n)):
        if i == 0:
            hook_indices.add(i)
            continue
        if _matches_any(beats[i].text, _HOOK_MARKERS) and i <= 1:
            hook_indices.add(i)

    # Identifica setup (após hook)
    setup_indices = set()
    setup_started = False
    for i in range(n):
        if i in hook_indices or i in payoff_indices:
            continue
        if _matches_any(beats[i].text, _SETUP_MARKERS):
            setup_started = True
            setup_indices.add(i)
        elif setup_started and len(setup_indices) < 3 and i not in hook_indices:
            # Continua setup por mais 1-2 beats após o gatilho
            setup_indices.add(i)
            if not _matches_any(beats[i].text, _SETUP_MARKERS):
                # após 1 beat de continuação, para
                setup_started = False

    # Identifica climax (marcadores de explosão)
    climax_indices = set()
    for i in range(n):
        if i in hook_indices or i in payoff_indices or i in setup_indices:
            continue
        if _matches_any(beats[i].text, _CLIMAX_MARKERS):
            climax_indices.add(i)

    # Atribui label final (precedência: HOOK > PAYOFF > CLIMAX > SETUP > ESCALADA)
    for i, b in enumerate(beats):
        if i in hook_indices:
            arch = "HOOK"
        elif i in payoff_indices:
            arch = "PAYOFF"
        elif i in climax_indices:
            arch = "CLIMAX"
        elif i in setup_indices:
            arch = "SETUP"
        else:
            arch = "ESCALADA"
        out.append(BeatArchetype(
            beat_index=b.index,
            archetype=arch,
            selection_hint=_SELECTION_HINTS[arch],
        ))

    return out


def render_archetypes_for_prompt(archetypes: List[BeatArchetype]) -> str:
    """Renderiza tabela de arquétipos pra injetar no prompt do matcher."""
    if not archetypes:
        return ""
    lines = ["ARQUÉTIPOS POR BEAT (use as diretivas de seleção pra cada tipo):"]
    for a in archetypes:
        lines.append(f"  {a.hint_line()}")
    return "\n".join(lines)
