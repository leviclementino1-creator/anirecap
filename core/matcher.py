"""Casa beats da narração com cenas do .mkv original via LLM.

A LLM já viu o transcript no resumo e o resumo no short_script; então ela
sabe sobre QUAL cena cada trecho da narração está falando. Basta pedir
explicitamente os timestamps.
"""
import json
import os
import re
from dataclasses import dataclass, field
from typing import List, Optional

from core.chunking import NarrationBeat
from core.cue import Cue
from core.face_detect import FaceDetector
from core.scene_detect import snap_to_scene
from providers import navy


MATCHER_PROMPT = """\
Você é um editor de vídeo fazendo um short de anime. Seu trabalho: pra cada
bloco curto da narração (beat), identificar em qual momento do episódio
original aquela cena está acontecendo.

CONTEXTO (resumo do que aconteceu):
{summary}

CENAS DO EPISÓDIO (grupos numerados com timestamps e diálogo representativo):
{cues_table}

BEATS DA NARRAÇÃO (pra cada um, escolha UMA cena acima):
{beats_table}

REGRAS IMPORTANTES:
1. VOCÊ DEVE RETORNAR UM MATCH PRA CADA UM DOS {n_beats} BEATS. Não pule nenhum.
2. Pra cada beat, escolha o `cue_id` (número da cena) que melhor representa
   VISUALMENTE o que está sendo narrado naquele beat.
   REQUISITO: as cues têm textos específicos; leia-os e escolha a cue cujo
   conteúdo mais se aproxima do que o beat narra. Não escolha por proximidade
   temporal, escolha por CORRESPONDÊNCIA SEMÂNTICA.
3. REGRA DE OURO — narração → imagem:
   - Se o beat é um HOOK/IMPACTO ("esse otaku saiu com DUAS garotas"),
     escolha a cena que MOSTRA visualmente esse impacto (as duas garotas
     com ele em quadro), NÃO a cena onde se fala sobre isso em palavras.
   - Se o beat é SETUP ("ele recebeu dois convites"), aí sim escolha a cena
     que MOSTRA o setup (ele olhando os convites, confuso).
   - Pense: "se eu fosse editor pausando no meio dessa narração, que FRAME
     o espectador precisa estar vendo pra história fazer sentido?"
4. REGRA DE CITAÇÃO: quando a narração tem uma FALA LITERAL entre aspas ou
   reproduzindo algo que um personagem diz ("tenho um pequeno interesse em
   você", "nada de coisas sujas!", "ué, como assim?"), ACHE A CUE onde essa
   fala está sendo dita no original (mesmo em inglês). Se o narrador cita
   "dirty" ou "sujas", procure uma cue com a palavra "dirty". Essa é a
   âncora mais precisa que existe.
4. Pode repetir cenas se beats consecutivos falam da mesma coisa.
5. Pode ser não-linear: a narração reorganiza a história — cenas podem
   aparecer fora da ordem cronológica do episódio.
6. EVITE cenas de estabelecimento, paisagem, céu, transições vazias.
   Prefira SEMPRE cenas com PERSONAGENS VISÍVEIS em quadro.
7. Se o beat narra um momento dramático, PRIORIZE a cena de reação/ação
   VISUAL daquele momento sobre a cena onde se menciona ele em diálogo.
8. O campo `why` é curto (3-7 palavras). NÃO USE aspas duplas " dentro do
   valor — use aspas simples ' ou parênteses. NÃO use quebras de linha.

SAÍDA: APENAS JSON válido. Começa direto com `{{`. Inclui OBRIGATORIAMENTE
uma entrada "matches" com EXATAMENTE {n_beats} itens (beats de 1 a {n_beats}):

{{
  "matches": [
    {{"beat": 1, "cue_id": 42, "why": "cena do convite da Amane"}},
    {{"beat": 2, "cue_id": 45, "why": "..."}},
    ...
    {{"beat": {n_beats}, "cue_id": N, "why": "..."}}
  ]
}}

Sem markdown, sem texto antes ou depois do JSON.
"""


@dataclass
class SceneMatch:
    """Um beat mapeado pra uma janela no .mkv original."""
    beat: NarrationBeat
    cue: Optional[Cue]  # None se o matcher falhou
    video_start: float
    video_end: float
    snapped: bool = False
    why: str = ""

    def label(self) -> str:
        snap = " ⇲" if self.snapped else ""
        return (
            f"beat {self.beat.index:02d}  "
            f"\"{self.beat.text[:50]}{'...' if len(self.beat.text) > 50 else ''}\"  "
            f"→  mkv {self.video_start:6.2f}-{self.video_end:6.2f}s{snap}  "
            f"({self.why})"
        )


def group_cues(cues: List[Cue], max_gap: float = 2.0) -> List[Cue]:
    """Agrupa cues consecutivas com gap < max_gap numa única "cena".
    Reduz pela metade ou mais o volume de tokens no prompt e dá contexto
    mais rico (cada entrada = bloco de diálogo contínuo).

    A start/end do grupo = do primeiro ao último. O texto é concatenado.
    """
    if not cues:
        return []
    groups: List[List[Cue]] = [[cues[0]]]
    for c in cues[1:]:
        gap = c.start - groups[-1][-1].end
        if gap < max_gap:
            groups[-1].append(c)
        else:
            groups.append([c])

    merged: List[Cue] = []
    for grp in groups:
        merged.append(Cue(
            start=grp[0].start,
            end=grp[-1].end,
            text=" ".join(c.text for c in grp),
        ))
    return merged


def _cues_table(cues: List[Cue], max_chars_each: int = 120) -> str:
    """Renderiza as cenas agrupadas como tabela pro prompt."""
    lines = []
    for i, c in enumerate(cues):
        text = c.text[:max_chars_each].replace("\n", " ")
        dur = c.end - c.start
        lines.append(f"[{i+1}] {c.start:7.2f}-{c.end:7.2f} ({dur:4.1f}s) | {text}")
    return "\n".join(lines)


def _beats_table(beats: List[NarrationBeat]) -> str:
    lines = []
    for b in beats:
        text = b.text.replace("\n", " ")
        lines.append(f"[{b.index}] narr {b.start:6.2f}-{b.end:6.2f}s | {text}")
    return "\n".join(lines)


_REGEX_MATCH = re.compile(
    r'"beat"\s*:\s*(\d+)[^{}]*?"cue_id"\s*:\s*(\d+)'
    r'(?:[^{}]*?"why"\s*:\s*"([^"\n]*))?',
    flags=re.DOTALL,
)


def _parse_matches_resilient(body: str) -> list:
    """Tenta JSON estrito; se falhar, extrai por regex os pares beat/cue_id.

    LLMs às vezes produzem JSON quebrado por aspas não-escapadas no meio de
    strings. A regex captura os números essenciais mesmo em respostas sujas —
    é melhor recuperar 95% do plano do que perder 100%.
    """
    try:
        data = json.loads(body)
        return data.get("matches") or []
    except (json.JSONDecodeError, AttributeError):
        pass

    out = []
    seen = set()
    for m in _REGEX_MATCH.finditer(body):
        bi = int(m.group(1))
        if bi in seen:
            continue
        seen.add(bi)
        out.append({
            "beat": bi,
            "cue_id": int(m.group(2)),
            "why": (m.group(3) or "").strip() or "(recuperado via regex)",
        })
    return out


def _extract_json_block(text: str) -> str:
    """LLM às vezes embrulha em ```json ... ```. Extrai o objeto JSON de dentro."""
    text = text.strip()
    if text.startswith("```"):
        # Remove primeira linha tipo ```json e última ```
        lines = text.splitlines()
        if len(lines) >= 2:
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            text = "\n".join(lines).strip()
    # Pega do primeiro { até o último }
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return text[start:end + 1]
    return text


def match_beats_to_cues(
    beats: List[NarrationBeat],
    cues: List[Cue],
    summary: str,
    api_key: str,
    base_url: str,
    model: str,
    scene_changes: Optional[List[float]] = None,
    pad_before: float = 0.0,
    max_backward_snap: float = 3.0,
    max_forward_snap: float = 0.5,
    group_cues_gap: float = 2.0,
    mkv_path: str = "",
    avoid_landscape: bool = True,
    landscape_search_seconds: float = 3.0,
) -> List[SceneMatch]:
    """Monta prompt com cues agrupadas, chama LLM, valida cobertura, aplica
    snap bidirecional. Beats sem match da LLM herdam a cena do beat anterior
    (melhor que fallback proporcional pra continuidade visual).
    """
    if not beats:
        return []

    # Agrupa cues contíguas — reduz volume de tokens sem perder contexto.
    # Gap menor = mais grupos = LLM tem mais opções específicas.
    grouped = group_cues(cues, max_gap=group_cues_gap)

    prompt = MATCHER_PROMPT.format(
        summary=(summary or "(sem resumo disponível)").strip(),
        cues_table=_cues_table(grouped),
        beats_table=_beats_table(beats),
        n_beats=len(beats),
    )

    raw = navy.chat_completion(
        api_key=api_key,
        base_url=base_url,
        model=model,
        messages=[{"role": "user", "content": prompt}],
        timeout=240.0,
        # Sem temperature=0: a LLM precisa ser criativa pra escolher entre
        # "cena literal onde se menciona o evento" vs "cena visualmente
        # impactante do evento". Determinismo aqui matava criatividade.
    )

    body = _extract_json_block(raw)
    raw_matches = _parse_matches_resilient(body)
    by_beat = {int(m.get("beat")): m for m in raw_matches if m.get("beat") is not None}

    missing = [b.index for b in beats if b.index not in by_beat]

    scene_changes = scene_changes or []
    results: List[SceneMatch] = []
    for b in beats:
        m = by_beat.get(b.index)
        cue = None
        why = ""
        if m is not None:
            cue_id = int(m.get("cue_id") or 0)
            if 1 <= cue_id <= len(grouped):
                cue = grouped[cue_id - 1]
            why = str(m.get("why") or "").strip()

        if cue is None:
            # Fallback de CONTINUIDADE: usa a cena do beat anterior (se houver).
            # Muito melhor do que "proporcional" pra não quebrar o ritmo visual.
            prev = results[-1] if results else None
            if prev and prev.cue:
                cue = prev.cue
                why = why or "fallback: continuidade do beat anterior"
            elif grouped:
                cue = grouped[0]
                why = why or "fallback: primeira cena"

        video_start_raw = (cue.start if cue else 0.0) - pad_before
        video_start_raw = max(0.0, video_start_raw)

        video_start = snap_to_scene(
            video_start_raw, scene_changes,
            max_backward=max_backward_snap, max_forward=max_forward_snap,
        )
        snapped = abs(video_start - video_start_raw) > 0.01

        video_end = video_start + b.duration

        results.append(SceneMatch(
            beat=b, cue=cue,
            video_start=video_start, video_end=video_end,
            snapped=snapped, why=why,
        ))

    if missing:
        # Anexa aviso no why do primeiro fallback pra ser visível no log
        for r in results:
            if r.beat.index in missing:
                r.why = f"[LLM pulou] {r.why}"

    # Pós-processa: quando N beats consecutivos caem na mesma cena agrupada,
    # distribui os video_start ao longo da duração da cena para evitar plano
    # parado. Cada start re-snappa pra mudança de cena natural mais próxima.
    _spread_consecutive_same_cue(
        results, scene_changes,
        max_backward=max_backward_snap,
        max_forward=max_forward_snap,
    )

    # Snap pra cue original (subtítulo individual) mais próxima: depois do
    # spread, cada video_start fica na média do grupo — mas a fala específica
    # está numa cue específica. Snap pra cue real dá precisão semântica
    # (começa exatamente quando alguém fala algo naquele intervalo).
    _snap_to_nearest_cue(results, cues, max_drift=4.0)

    # Fuzzy NARROW: só pra beats com aspas (fala literal citada). Ajuda
    # casos tipo "nada de coisas sujas" → cue com "dirty" exato.
    _fuzzy_refine_quoted_beats(results, cues)

    # Safety net anti-landscape: verifica se cada video_start tem rosto; se
    # não, tenta mover pra próxima scene change dentro da janela.
    if avoid_landscape and mkv_path and os.path.isfile(mkv_path):
        _avoid_landscape_pass(
            results, mkv_path, scene_changes, landscape_search_seconds,
        )

    return results


# Palavras PT com tradução EN RARA/ESPECÍFICA — se a narração tem uma
# dessas, fuzzy pode ancorar com segurança. "ué", "como assim", "irmãzinha"
# foram REMOVIDAS porque suas traduções ("what", "huh", "sister") são
# comuns demais e geram falsos positivos.
_PT_EN_HIGH_VALUE = {
    "sujas": ["dirty"], "sujo": ["dirty"],
    "abrace": ["hug", "arm around"],
    "abraço": ["hug", "arm around"],
    "abraçar": ["hug", "arm around"],
    "glittermon": ["glittermon"],
    "fanbook": ["fan book", "fanbook"],
    "animate": ["animate"],
    "fliperama": ["arcade"],
    "hotel": ["hotel"],
    "gamada": ["crush", "into him", "in love"],
    "dançando": ["dancing"],
    "dancinha": ["dance", "dancing"],
    "dancinhas": ["dance", "dancing"],
    "miss luna": ["miss luna"],
    "standee": ["standee", "acrylic"],
    "caretas": ["funny face", "silly face"],
}


def _fuzzy_refine_quoted_beats(
    results: List[SceneMatch],
    all_cues: List[Cue],
    min_distance_to_override: float = 10.0,
) -> int:
    """Fuzzy NARROW e ESPECÍFICO.

    Condições pra disparar:
    1. Beat tem aspas (citação literal na narração)
    2. Beat contém palavra PT de alto valor (_PT_EN_HIGH_VALUE) — palavras
       raras com tradução específica. Palavras genéricas como 'ué' ou
       'irmãzinha' não disparam (geram falsos positivos).
    3. Encontra uma cue com PELO MENOS 1 keyword-hit a mais que a atual.
    4. Distância temporal > min_distance_to_override.

    Só então sobrescreve.
    """
    moved = 0

    for m in results:
        text = m.beat.text
        if '"' not in text:
            continue

        text_lower = text.lower()

        # Extrai APENAS keywords de alto valor
        en_keywords = set()
        for pt_word, en_list in _PT_EN_HIGH_VALUE.items():
            if pt_word in text_lower:
                en_keywords.update(en_list)

        if not en_keywords:
            continue  # sem palavra-chave forte, skip fuzzy

        # Score cue atual
        current_score = 0
        if m.cue:
            cue_lower = m.cue.text.lower()
            current_score = sum(1 for kw in en_keywords if kw.lower() in cue_lower)

        # Score todas as cues
        best = None
        best_score = current_score
        for cue in all_cues:
            cue_lower = cue.text.lower()
            score = sum(1 for kw in en_keywords if kw.lower() in cue_lower)
            if score > best_score:
                best = cue
                best_score = score

        if best is None:
            continue
        if best_score - current_score < 1:
            continue
        if abs(best.start - m.video_start) < min_distance_to_override:
            continue

        m.video_start = best.start
        m.video_end = best.start + m.beat.duration
        m.snapped = True
        m.why = f"[fuzzy-quote +{best_score}kw] {m.why}"
        moved += 1

    return moved


def _snap_to_nearest_cue(
    results: List[SceneMatch],
    cues: List[Cue],
    max_drift: float = 2.5,
) -> None:
    """Move cada match pro timestamp de uma cue original (subtítulo
    individual) dentro de `max_drift` segundos. Garante que cada beat
    começa num momento onde uma fala REAL existe no episódio, não num
    ponto arbitrário do grupo.

    Evita usar a mesma cue pra 2 beats diferentes (cada um pega uma cue
    exclusiva, quando possível).
    """
    if not cues:
        return

    used = set()
    # Ordena por video_start pra processar na ordem temporal do short
    # (beats iniciais pegam primeiro a cue mais próxima)
    by_order = sorted(range(len(results)), key=lambda i: results[i].video_start)

    for idx in by_order:
        m = results[idx]
        target = m.video_start
        candidates = [
            c for c in cues
            if abs(c.start - target) <= max_drift and c.start not in used
        ]
        if not candidates:
            continue
        best = min(candidates, key=lambda c: abs(c.start - target))
        if abs(best.start - target) > 0.05:  # só move se mudança significativa
            m.video_start = best.start
            m.video_end = best.start + m.beat.duration
            used.add(best.start)
        else:
            used.add(best.start)  # marca como ocupada mesmo sem mover


def _avoid_landscape_pass(
    results: List[SceneMatch],
    mkv_path: str,
    scene_changes: List[float],
    search_seconds: float,
) -> int:
    """Pra cada match, testa se tem rosto no video_start. Se não, busca a
    próxima scene change dentro de `search_seconds` que tenha rosto e muda
    pra lá. Depois de mover um beat, propaga o deslocamento pros beats
    imediatamente seguintes que estavam STITCHADOS na mesma cena (senão
    eles ficam no timestamp antigo e o vídeo pula pra trás).

    Devolve número de matches movidos.
    """
    detector = FaceDetector()
    if not detector.available or detector._cascade is None:
        return 0

    moved = 0
    for i, m in enumerate(results):
        if detector.has_face_at(mkv_path, m.video_start):
            continue

        # Sem rosto — tenta scene changes adiante
        candidates = [
            s for s in scene_changes
            if m.video_start < s <= m.video_start + search_seconds
        ]
        new_start = None
        for s in candidates:
            if detector.has_face_at(mkv_path, s):
                new_start = s
                break

        if new_start is None:
            m.why = f"[sem rosto detectado] {m.why}"
            continue

        # Move beat atual
        m.video_start = new_start
        m.video_end = new_start + m.beat.duration
        m.snapped = True
        m.why = f"[no-face→face {search_seconds:.0f}s adiante] {m.why}"
        moved += 1

        # Propaga pra stitched seguintes (mesma cue, consecutivos após i).
        # Sem isso o beat i+1 continua no video_start antigo e o render pula
        # pra trás no meio do short.
        j = i + 1
        while j < len(results) and results[j].cue is m.cue:
            prev = results[j - 1]
            results[j].video_start = prev.video_end
            results[j].video_end = results[j].video_start + results[j].beat.duration
            results[j].snapped = False  # continuação, não snap novo
            j += 1

    return moved


def _spread_consecutive_same_cue(
    results: List[SceneMatch],
    scene_changes: List[float],
    max_backward: float,
    max_forward: float,
) -> None:
    """Spread puro: para cada cue usada por múltiplos beats (consecutivos ou
    não), distribui os `video_start` igualmente ao longo da duração da cena.

    Consequência: beats consecutivos na mesma cena mostram MOMENTOS DIFERENTES
    dela em vez de uma tomada contínua longa. Ideal pro ritmo de short —
    evita planos de 10-15s segurados só porque N beats caíram na mesma cue.

    Versão anterior fazia stitch (continuação) pros consecutivos, criando
    tomadas longas. Agora é sempre spread.
    """
    if len(results) < 2:
        return

    # Agrupa TODOS os índices de beats por cue (em ordem de narração)
    cue_indices = {}
    for idx, m in enumerate(results):
        if m.cue is None:
            continue
        key = id(m.cue)
        cue_indices.setdefault(key, []).append(idx)

    for key, indices in cue_indices.items():
        if len(indices) < 2:
            continue  # único beat nessa cue — não mexer

        cue = results[indices[0]].cue
        cue_dur = max(cue.end - cue.start, 0.1)
        n = len(indices)
        step = cue_dur / n  # distribui uniformemente ao longo da cena

        for k, idx in enumerate(indices):
            m = results[idx]
            raw = cue.start + k * step
            snapped = snap_to_scene(
                raw, scene_changes,
                max_backward=max_backward, max_forward=max_forward,
            )
            # Só aplica se for materialmente diferente da posição atual
            if abs(snapped - m.video_start) > 0.3:
                m.video_start = snapped
                m.snapped = abs(snapped - raw) > 0.01
                m.video_end = m.video_start + m.beat.duration
