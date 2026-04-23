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
   visualmente o que está sendo narrado naquele beat.
3. Pode repetir cenas se beats consecutivos falam da mesma coisa.
4. Pode não-linear: a narração reorganiza a história, cenas podem aparecer
   fora de ordem cronológica — isso é esperado.
5. Se o beat é um comentário do narrador que não tem cena específica, escolha
   a cena que está sendo comentada ali perto na narrativa — NUNCA deixe sem match.
6. EVITE cenas de estabelecimento, paisagem, céu, transições vazias ou
   qualquer cena sem personagens visíveis. Prefira SEMPRE cenas com diálogo
   ativo ou personagens em foco. Se o short_script menciona um personagem,
   escolha a cena ONDE ele aparece, não a cena que só prepara o cenário.
7. O campo `why` é curto (3-7 palavras). NÃO USE aspas duplas " dentro do
   valor — se precisar citar algo, use aspas simples ' ou parênteses. NÃO use
   quebras de linha dentro do valor.

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
        temperature=0,  # determinismo: mesmo input sempre dá mesmo output
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

    # Safety net anti-landscape: verifica se cada video_start tem rosto; se
    # não, tenta mover pra próxima scene change dentro da janela.
    if avoid_landscape and mkv_path and os.path.isfile(mkv_path):
        _avoid_landscape_pass(
            results, mkv_path, scene_changes, landscape_search_seconds,
        )

    return results


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
    """Stitch + spread.

    Algoritmo:
    1. Identifica RUNS — grupos de beats CONSECUTIVOS que apontam pra mesma cue.
    2. Múltiplas runs da mesma cue são distribuídas na duração da cena
       (run 0 no start, run 1 a 1/N da cena, etc.).
    3. Dentro de uma run, o primeiro beat pega o start distribuído (com snap);
       os seguintes STITCHAM — cada um começa exatamente onde o anterior acabou.

    Efeito visual:
    - 3 beats consecutivos na mesma cena → uma tomada contínua de 7.5s
    - 2 beats não-consecutivos na mesma cena → duas tomadas diferentes dela
    - Mix → cada run stitcha internamente; runs entre si ficam em pontos distintos
    """
    if len(results) < 2:
        return

    # 1. Identifica runs consecutivas
    runs = []  # List[Tuple[cue_identity, start_idx, end_idx_exclusive]]
    i = 0
    while i < len(results):
        j = i + 1
        while j < len(results) and results[j].cue is results[i].cue:
            j += 1
        runs.append((id(results[i].cue) if results[i].cue else None, i, j))
        i = j

    # 2. Agrupa runs por cue identity
    runs_by_cue = {}
    for cue_key, s, e in runs:
        if cue_key is None:
            continue
        runs_by_cue.setdefault(cue_key, []).append((s, e))

    # 3. Pra cada cue com múltiplas runs, distribui os starts de cada run
    for cue_key, run_list in runs_by_cue.items():
        cue = results[run_list[0][0]].cue
        cue_dur = max(cue.end - cue.start, 0.1)
        n_runs = len(run_list)
        step = cue_dur / n_runs if n_runs > 1 else 0.0

        for k, (rs, re) in enumerate(run_list):
            # Primeiro beat da run: pega o slot distribuído (se houver >1 run)
            if n_runs > 1:
                raw = cue.start + k * step
                snapped = snap_to_scene(
                    raw, scene_changes,
                    max_backward=max_backward, max_forward=max_forward,
                )
                first = results[rs]
                if abs(snapped - first.video_start) > 0.3:
                    first.video_start = snapped
                    first.snapped = abs(snapped - raw) > 0.01
                    first.video_end = first.video_start + first.beat.duration

            # Beats subsequentes da run: STITCHAM (continuação contínua)
            for idx in range(rs + 1, re):
                prev = results[idx - 1]
                curr = results[idx]
                curr.video_start = prev.video_end
                curr.video_end = curr.video_start + curr.beat.duration
                # Sem snap — é continuação, não corte novo
                curr.snapped = False
