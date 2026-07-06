"""Casa beats da narração com cenas do .mkv original via LLM.

A LLM já viu o transcript no resumo e o resumo no short_script; então ela
sabe sobre QUAL cena cada trecho da narração está falando. Basta pedir
explicitamente os timestamps.
"""
import json
import os
import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from core.chunking import NarrationBeat
from core.cue import Cue
from core.face_detect import FaceDetector
from core.scene_detect import snap_to_scene, find_clean_window
from providers import navy


# Versão semântica do prompt — cache key usa ISSO em vez do prompt literal.
# Bumpar quando a mudança deve invalidar caches (nova regra, regra alterada).
# Ajustes cosméticos (typo, reword) NÃO precisam bumpar.
MATCHER_PROMPT_VERSION = "matcher-v10-confidence-runnerup-2026-07-06"


MATCHER_PROMPT = """\
Você é um editor de vídeo fazendo um short de anime. Seu trabalho: pra cada
bloco curto da narração (beat), identificar em qual momento do episódio
original aquela cena está acontecendo.

CONTEXTO (resumo do que aconteceu):
{summary}

{visual_glossary_section}

{archetypes_section}

CENAS DO EPISÓDIO (grupos numerados com timestamps e diálogo OU descrição visual):
{cues_table}

NOTA SOBRE TAGS DE ÁUDIO 🔊:
Algumas linhas terminam com tags como 🔊LOUD, 🔊HIGH-ACTION, 🔊DRAMATIC-POP.
Essas tags vêm do envelope de áudio do .mkv original (sem CV) e são PROXY
DE INTENSIDADE VISUAL:
- 🔊LOUD → música ou SFX em pico = cena visualmente carregada
- 🔊HIGH-ACTION → muitos onsets = sequência de impactos físicos (ação)
- 🔊DRAMATIC-POP → silêncio sustentado seguido de pico = MOMENTO REVELAÇÃO
  (frames mais memoráveis do ep — preferência alta pra HOOK e CLIMAX)
- 🔊quiet → áudio calmo = cena contemplativa/setup

REGRA PRÁTICA: pra beats classificados como HOOK ou CLIMAX (veja arquétipos
acima), PREFIRA cues com 🔊LOUD, 🔊HIGH-ACTION ou 🔊DRAMATIC-POP. Pra SETUP
ou PAYOFF contemplativo, 🔊quiet é OK. Pra ESCALADA, prefira HIGH-ACTION.

NOTA SOBRE CUES `[VISUAL]` E `[SILÊNCIO]`:
Linhas com prefixo `[VISUAL]` são descrições narradas do que APARECE NA TELA
(audio description, feita pra acessibilidade). Linhas com texto começando
em `[SILÊNCIO]` são pseudo-cues geradas onde há scene change SEM diálogo —
sinalizam cenas visuais silenciosas (thought bubbles, transições, imaginação,
reflexão). Linhas sem nenhum prefixo são falas de personagem. TODAS são
cue_ids válidos que você pode escolher.

QUANDO PREFERIR CUE `[SILÊNCIO]`:
- Beat descreve IMAGINAÇÃO/THOUGHT BUBBLE ("ele imagina...", "sonhando
  com..."). A thought bubble aparece visualmente em cenas sem fala.
- Beat descreve TRANSIÇÃO VISUAL ou REFLEXÃO sem diálogo ("ele olha o
  céu", "ela respira fundo", cenas estabelecedoras).
- Beat descreve REAÇÃO SILENCIOSA prolongada ("ela paralisa", "olhar
  perdido"). Cuidado: se há fala próxima, prefira a cue de fala que
  acompanha a reação.

QUANDO PREFERIR CUE `[VISUAL]`:
- Beat descreve AÇÃO FÍSICA que acontece na tela (ex: "Agathe solta fogo",
  "dragão ruge", "Coco corre atrás", "paredes viram brancas", "ela cai").
- Beat descreve REAÇÃO FACIAL/EMOÇÃO sem diálogo (ex: "Coco arregala os
  olhos", "Agathe olha com raiva").
- O beat menciona um evento CUJA FALA correspondente não existe ou está
  longe do momento visual (ex: narração fala de fogo mas o diálogo mais
  próximo com "fogo" é 10s depois, quando Coco PERGUNTA sobre o feitiço —
  prefira a cue `[VISUAL]` que diz "uma grande chama surge" no momento real).

QUANDO PREFERIR CUE DE DIÁLOGO (sem prefixo):
- Beat cita uma FALA LITERAL ("ele disse X", "ela gritou Y").
- Beat descreve CONVERSA, EXPLICAÇÃO, REVELAÇÃO verbal.
- Beat é meta-narrativo sobre o que um personagem PENSA/DIZ.

EXEMPLO CONCRETO:
- Beat: "Agathe tenta distrair o dragão com feitiço de fogo"
- Na tabela: `[45] 980s | [VISUAL] uma grande chama surge` + `[47] 991s | Usei o emblema do fogo`
- Escolha CUE 45 (VISUAL) — é a cena do fogo realmente acontecendo.
- Escolher 47 mostraria a Coco perguntando DEPOIS, sem ver o fogo no quadro.

BEATS DA NARRAÇÃO (pra cada um, escolha UMA cena acima):
{beats_table}

REGRAS IMPORTANTES:
1. VOCÊ DEVE RETORNAR UM MATCH PRA CADA UM DOS {n_beats} BEATS. Não pule nenhum.

2. SOBRE O `cue_id` (LEIA COM ATENÇÃO — ERRO COMUM AQUI):
   Cada linha da tabela começa com `CUE=N` (onde N é um número inteiro
   pequeno, de 1 até o total de cenas). ESSE N é o `cue_id` que você deve
   retornar. NÃO retorne o timestamp (mm:ss) ou qualquer outro número.
   EXEMPLO: Se a tabela tem:
     CUE=109  | 18:53.88-19:19.45 | algum texto aqui
   O `cue_id` correto é `109`, NÃO `1133`, NÃO `18`, NÃO `1853`.
   Se você retornar um número maior que o total de cenas, seu match vai
   ser rejeitado.

3. Pra cada beat, escolha o `cue_id` que melhor representa VISUALMENTE o
   que está sendo narrado naquele beat.
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
8. COLD OPEN / TEASER + HOOK CLIMAX — REGRA ABSOLUTA:
   Animes abrem com um COLD OPEN/TEASER (primeiros 1-5 minutos antes da
   música de abertura). Ele costuma MOSTRAR FRAGMENTOS e REPETIR FALAS do
   clímax do episódio pra criar expectativa. A MESMA fala aparece duas
   vezes: no teaser (timestamp baixo) e no clímax real (timestamp alto).
   REGRAS OBRIGATÓRIAS:
   (a) Para os PRIMEIROS 1-2 beats da narração (que costumam ser o HOOK
       tipo "essa garota foi acusada de X"), JAMAIS escolha cue com
       timestamp < 300s. Se o match "perfeito" está em 5s, IGNORE-O e
       procure a mesma fala/cena em timestamp >600s. A primeira vez é
       teaser, a segunda vez é o clímax real.
   (b) HOOKS DE ACUSAÇÃO ("X foi acusada de Y", "X foi chamada de peso
       morto", "X é a culpada de Z", "descobriu que W traiu") quase
       sempre se referem ao CLÍMAX EMOCIONAL do episódio, que acontece no
       ÚLTIMO TERÇO do ep. Pra esses hooks, escolha cue com timestamp
       no ÚLTIMO TERÇO (após 60% da duração total do ep). Ex: ep de 24min
       (1440s) → procure após 864s. Cuidado especial: escolher uma cue
       no MEIO do ep só porque tem palavra em comum (ex: "Agott grita")
       pode ser ERRO — Agott pode ter gritado várias vezes durante a
       perseguição. O grito que IMPORTA pro hook é o do CLÍMAX (a
       explosão emocional final).
   (c) Em geral, se uma fala aparece duas vezes (teaser + clímax), SEMPRE
       escolha a TARDIA (maior timestamp). A regra vale pra QUALQUER beat,
       não só o hook.
   (d) Beats de SETUP (meio da narração) podem usar cues pré-OP se forem
       cenas legítimas (aula, apresentação de personagem, treino). Não
       confunda setup legítimo (ex: "Coco tentou um feitiço" em 73s) com
       flash de teaser (ex: "Agathe gritou sobre a mãe" em 5s — teaser
       porque clímax real é em 1145s).
   (e) Exemplo CONCRETO: beat 01 "essa garota foi acusada de petrificar a
       mãe" casa com cue em 5s (teaser), cue em 707s (Agathe gritando na
       perseguição) e cue em 1145s (Agathe explodindo no beco no clímax).
       ESCOLHA 1145s OBRIGATORIAMENTE — é o momento da ACUSAÇÃO REAL.
       Escolher 5s ou 707s é ERRO CRÍTICO.
9. REGRA DE SÍMBOLO VISUAL — IMPORTANTE PRA QUALIDADE EDITORIAL:
   Quando o beat menciona um OBJETO ICÔNICO ou ELEMENTO VISUAL FORTE,
   PREFIRA cue `[VISUAL]` que MOSTRA esse objeto/elemento em quadro,
   em vez de cue de DIÁLOGO onde alguém apenas FALA sobre ele.

   OBJETOS ICÔNICOS comuns: livro, varinha, espada, machado, arma, bússola,
   chapéu, máscara, espelho, foto, carta, mapa, relíquia, anel, medalhão,
   símbolo mágico, pentagrama, runa, tatuagem, cicatriz, fogo, chama,
   relâmpago, água, sangue, fumaça, portal, cristal, espada, etc.
   ELEMENTOS VISUAIS FORTES: explosão, queda, voo, beijo, abraço, lágrima,
   sorriso, olhar, transformação, queda de objetos, derramamento.

   EDITORES DE SHORT FAZEM ISSO INSTINTIVAMENTE: imagem evocativa do
   objeto > cabeça falando sobre ele. Mostra > conta.

   Atenção: o objeto na cue [VISUAL] não precisa ser EXATAMENTE o mesmo
   objeto literal mencionado no beat — basta ser um objeto da MESMA
   CATEGORIA evocativa.
   - Beat: "o feiticeiro deu o livro proibido"
     CUE=72 | 12:55 | Coco diz: "Eu vi quem me deu o livro!"
     CUE=1  | 00:00 | [VISUAL] Um livro se abre
     ESCOLHA: CUE=1. O livro do CUE=1 não é literalmente o "livro proibido"
     (é o livro de magia da aula), MAS visualmente simboliza "o livro" que
     o beat narra. É 1000× mais impactante que cabeça falando.
   - Beat: "Agathe solta uma chama gigante"
     CUE=93 | 16:31 | Coco diz: "Você usou o emblema do fogo?"
     CUE=10 | 16:20 | [VISUAL] Uma grande chama irrompe
     ESCOLHA: CUE=10. A chama em quadro > Coco perguntando depois.

   Quando NÃO aplicar essa regra:
   - Beat é meta-narrativo ("essa garota foi acusada de X") sem objeto.
   - Beat é puramente conversacional ("ele disse que ela é traidora").
   - Beat de revelação/diálogo verbal sem componente visual forte.

10. O campo `why` é curto (3-7 palavras). NÃO USE aspas duplas " dentro do
    valor — use aspas simples ' ou parênteses. NÃO use quebras de linha.

11. CONFIDENCE + RUNNER-UP (obrigatório em CADA match):
    - "confidence": inteiro 1-5 = quão certo você está de que a cue escolhida
      mostra VISUALMENTE o que o beat narra.
        5 = certeza (fala literal citada ou ação explícita na cue)
        4 = forte correspondência
        3 = razoável, mas existe outra cue plausível
        2 = dúvida real entre 2+ cues
        1 = chute (nenhuma cue casa bem)
      SEJA HONESTO — confidence baixa é ÚTIL (o usuário revisa esses beats
      primeiro no editor). NÃO infle o número.
    - "runner_up_cue_id": a SEGUNDA melhor cue pra esse beat (inteiro,
      diferente de cue_id). OBRIGATÓRIA quando confidence <= 3. Use null
      quando confidence >= 4 e não existe alternativa realista.

SAÍDA: APENAS JSON válido. Começa direto com `{{`. Inclui OBRIGATORIAMENTE
uma entrada "matches" com EXATAMENTE {n_beats} itens (beats de 1 a {n_beats}):

{{
  "matches": [
    {{"beat": 1, "cue_id": 42, "confidence": 5, "runner_up_cue_id": null, "why": "cena do convite da Amane"}},
    {{"beat": 2, "cue_id": 45, "confidence": 2, "runner_up_cue_id": 17, "why": "..."}},
    ...
    {{"beat": {n_beats}, "cue_id": N, "confidence": N, "runner_up_cue_id": N, "why": "..."}}
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
    # Quão seguro o LLM está do match (1=chute, 5=certeza; 0=não informado).
    # Beats com confidence <= 2 aparecem destacados no editor de plano.
    confidence: int = 0
    # Timestamp da 2ª melhor opção do LLM (-1 = nenhuma). O editor de plano
    # oferece como candidato "🥈 opção B" na troca de cena.
    runner_up_start: float = -1.0

    def label(self) -> str:
        snap = " ⇲" if self.snapped else ""
        conf = f" c{self.confidence}" if self.confidence else ""
        return (
            f"beat {self.beat.index:02d}  "
            f"\"{self.beat.text[:50]}{'...' if len(self.beat.text) > 50 else ''}\"  "
            f"→  mkv {self.video_start:6.2f}-{self.video_end:6.2f}s{snap}{conf}  "
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


def _cues_table(
    cues: List[Cue],
    max_chars_each: int = 120,
    ad_indices: Optional[set] = None,
    silence_indices: Optional[set] = None,
    audio_envelope=None,
) -> str:
    """Renderiza as cenas agrupadas como tabela pro prompt.

    Se `ad_indices` é fornecido (set de cue_ids 1-based), essas linhas
    recebem prefixo `[VISUAL]` indicando que vêm de audio description.
    Se `silence_indices` é fornecido, recebem prefixo `[SILÊNCIO]` —
    pseudo-cues de cenas sem fala (geradas por scene changes em gaps
    de diálogo).

    Se `audio_envelope` é fornecido (AudioEnvelope), cada cue ganha tags
    de intensidade do áudio do .mkv original — proxy de impacto visual
    sem CV. Tags possíveis: LOUD, quiet, HIGH-ACTION, DRAMATIC-POP.
    """
    ad_indices = ad_indices or set()
    silence_indices = silence_indices or set()
    lines = []
    for i, c in enumerate(cues):
        text = c.text[:max_chars_each].replace("\n", " ")
        # Formato mm:ss em vez de segundos absolutos — reduz chance de LLM
        # confundir timestamp (ex: 1133.88) com cue_id numérico.
        start_mm = int(c.start // 60)
        start_ss = c.start - start_mm * 60
        end_mm = int(c.end // 60)
        end_ss = c.end - end_mm * 60
        mark = ""
        if (i + 1) in ad_indices:
            mark = "[VISUAL] "
        elif (i + 1) in silence_indices:
            mark = ""  # texto da cue ja comeca com [SILÊNCIO]

        # Audio intensity tag — proxy de impacto visual.
        # Aplicada no MEIO da cue pra captar o pico, não a borda.
        audio_tag = ""
        if audio_envelope is not None:
            mid_t = (c.start + c.end) / 2.0
            features = audio_envelope.features_at(mid_t)
            tag = features.short_tag()
            if tag:
                audio_tag = f" 🔊{tag}"

        # `CUE=N` em vez de `[N]` — formato explícito pra o LLM não
        # achar que o cue_id é algum outro número na linha.
        lines.append(
            f"CUE={i+1:<4}| {start_mm:02d}:{start_ss:05.2f}-{end_mm:02d}:{end_ss:05.2f} | "
            f"{mark}{text}{audio_tag}"
        )
    return "\n".join(lines)


def _visual_hints_block(ad_cues, max_chars_each=100, max_hints=200):
    """OBSOLETO — mantido só pra compat. AD agora entra mesclada com cues
    como selecionável (via `_merge_subtitle_and_ad`). Retorna vazio."""
    return ""


def _merge_subtitle_and_ad(
    subtitle_cues: List[Cue],
    ad_cues: List[Cue],
) -> Tuple[List[Cue], set]:
    """Mescla cues de diálogo e AD cronologicamente numa única lista
    selecionável. Retorna (merged, ad_index_set).

    AD entra como cue normal com prefixo `[VISUAL]` no texto (adicionado no
    render). O LLM pode escolher tanto cue de diálogo quanto de AD,
    recebendo `cue_id` válido pra ambas. Isso permite que beats de AÇÃO
    VISUAL (fogo surge, dragão ruge, Coco corre) casem com o timestamp
    EXATO do AD, em vez de cair numa cue de diálogo próxima mas errada.

    `ad_index_set` é 1-based pra bater com `cue_id`. Usado no render da
    tabela pra prefixar `[VISUAL]` só nas linhas certas.
    """
    merged = sorted(subtitle_cues + ad_cues, key=lambda c: c.start)
    ad_ids = {id(c) for c in ad_cues}
    ad_set = set()
    for i, c in enumerate(merged):
        if id(c) in ad_ids:
            ad_set.add(i + 1)
    return merged, ad_set


def inject_silence_cues(
    cues: List[Cue],
    scene_changes: List[float],
    min_silence_gap: float = 5.0,
    min_silence_window: float = 1.5,
) -> Tuple[List[Cue], set]:
    """Injeta pseudo-cues [SILÊNCIO] em scene changes dentro de gaps longos
    sem diálogo. Sem AD, animes têm cenas silenciosas relevantes (thought
    bubbles, transições, reflexão visual) que não estão na tabela do matcher
    porque não têm fala. Essas pseudo-cues dão ao matcher acesso a esses
    momentos.

    Critério:
    - Gap entre cues consecutivas >= `min_silence_gap` (5s default)
    - Cada scene change DENTRO do gap vira uma pseudo-cue cobrindo do scene
      change até o próximo (ou até o fim do gap)
    - Pseudo-cue tem texto fixo "[SILÊNCIO] cena visual sem fala" pra
      sinalizar ao LLM que pode escolher pra beats descritivos
    - Pseudo-cue tem janela mínima de `min_silence_window` (1.5s) — abaixo
      disso é flash, ignora

    Retorna (cues_merged, silence_index_set) — set de cue_ids 1-based das
    pseudo-cues injetadas (pra renderizar com marcação no prompt).
    """
    if not cues or not scene_changes:
        return cues, set()

    cues_sorted = sorted(cues, key=lambda c: c.start)
    scenes_sorted = sorted(scene_changes)
    silence_pseudo: List[Cue] = []

    # Detecta gaps de silêncio entre cues consecutivas
    for i in range(len(cues_sorted) - 1):
        gap_start = cues_sorted[i].end
        gap_end = cues_sorted[i + 1].start
        if gap_end - gap_start < min_silence_gap:
            continue

        # Pega scene changes dentro do gap (excluindo bordas)
        scenes_in_gap = [
            s for s in scenes_sorted
            if gap_start + 0.2 < s < gap_end - 0.2
        ]
        if not scenes_in_gap:
            continue

        # Cria pseudo-cue por scene change (cobre até próximo scene change)
        boundaries = scenes_in_gap + [gap_end]
        for j in range(len(boundaries) - 1):
            ps_start = boundaries[j]
            ps_end = boundaries[j + 1]
            window = ps_end - ps_start
            if window < min_silence_window:
                continue  # cena curta = flash, pula
            silence_pseudo.append(Cue(
                start=ps_start,
                end=ps_end,
                text="[SILÊNCIO] cena visual sem fala",
            ))

    if not silence_pseudo:
        return cues, set()

    # Mescla e calcula set de indices das pseudo-cues
    merged = sorted(cues + silence_pseudo, key=lambda c: c.start)
    pseudo_ids = {id(c) for c in silence_pseudo}
    silence_set = set()
    for i, c in enumerate(merged):
        if id(c) in pseudo_ids:
            silence_set.add(i + 1)
    return merged, silence_set


def _beats_table(beats: List[NarrationBeat]) -> str:
    lines = []
    for b in beats:
        text = b.text.replace("\n", " ")
        lines.append(f"[{b.index}] narr {b.start:6.2f}-{b.end:6.2f}s | {text}")
    return "\n".join(lines)


_REGEX_MATCH = re.compile(
    r'"beat"\s*:\s*(\d+)[^{}]*?"cue_id"\s*:\s*(\d+)'
    r'(?:[^{}]*?"confidence"\s*:\s*(\d+))?'
    r'(?:[^{}]*?"runner_up_cue_id"\s*:\s*(\d+))?'
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
            "confidence": int(m.group(3)) if m.group(3) else 0,
            "runner_up_cue_id": int(m.group(4)) if m.group(4) else None,
            "why": (m.group(5) or "").strip() or "(recuperado via regex)",
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


# Stopwords PT pra extração de keywords. Curta porque queremos só palavras
# fortes pra validação (substantivos próprios + verbos de ação + termos raros).
_STOPWORDS_PT = {
    "a", "o", "as", "os", "um", "uma", "uns", "umas", "de", "da", "do", "das",
    "dos", "em", "na", "no", "nas", "nos", "com", "sem", "por", "pra", "para",
    "que", "qual", "quando", "como", "onde", "porque", "e", "ou", "mas", "se",
    "ela", "ele", "elas", "eles", "é", "são", "foi", "vai", "está", "estão",
    "tem", "têm", "ser", "estar", "fica", "ficou", "ficar", "uma", "outra",
    "outros", "outras", "isso", "isto", "esse", "essa", "esses", "essas",
    "muito", "pouco", "todo", "toda", "todos", "todas", "também", "ainda",
    "agora", "depois", "antes", "sobre", "entre", "fala", "diz", "disse",
    "fazer", "tudo", "nada", "algo", "alguém", "ninguém", "para", "tipo",
}


def _extract_keywords(text: str, min_len: int = 4, max_n: int = 4) -> List[str]:
    """Extrai até `max_n` palavras-chave fortes de um texto curto.

    Filtra stopwords e palavras muito curtas. Mantém substantivos próprios
    (mantém capitalização original), verbos de ação distintivos, e termos
    raros. Tudo lowercase pra comparação case-insensitive.
    """
    if not text:
        return []
    # Tokeniza preservando acentos
    tokens = re.findall(r"[a-zA-ZÀ-ÿ]+", text.lower())
    keywords = []
    for t in tokens:
        if len(t) < min_len:
            continue
        if t in _STOPWORDS_PT:
            continue
        keywords.append(t)
        if len(keywords) >= max_n:
            break
    return keywords


def _validate_matches_semantically(
    matches: list,
    grouped: List[Cue],
    min_chosen_misses: int = 0,   # quantas keywords PODEM faltar na escolhida (0 = nenhuma)
    min_score_gap: int = 2,       # candidato precisa ter >= esse a mais que escolhida
) -> list:
    """Validação CONSERVADORA: só substitui cue se há evidência forte de
    alucinação do LLM.

    Regra dispara só quando:
    - cue escolhida tem ZERO matches de keywords do why
    - existe outra cue com 2+ matches MAIS que a escolhida
    - mínimo 2 keywords totais no why (filtro contra whys curtos demais)

    Versão anterior (min_overlap=1) era agressiva e movia beats que o LLM
    havia escolhido corretamente. Esta versão só age em casos óbvios:
    why="Bruxa fala única esperança" + cue escolhida = "Kieffrey sobre porta-pena"
    → claramente alucinação, troca.

    Mas:
    why="Coco esbarra em Agathe" + cue escolhida = "Coco e Agathe na cena"
    (sem "esbarra" exato no texto) → não troca, talvez seja só fraseado
    diferente.
    """
    if not matches or not grouped:
        return matches

    cue_lower = [c.text.lower() for c in grouped]

    fixed = []
    for m in matches:
        try:
            cue_id = int(m.get("cue_id") or 0)
        except (ValueError, TypeError):
            fixed.append(m)
            continue

        why = str(m.get("why") or "").strip()
        keywords = _extract_keywords(why)

        # Filtro: precisa ter pelo menos 2 keywords pra validar.
        # Whys com 0-1 keywords são pouco distintivos pra busca confiável.
        if len(keywords) < 2:
            fixed.append(m)
            continue

        in_range = 1 <= cue_id <= len(grouped)
        chosen_text = cue_lower[cue_id - 1] if in_range else ""

        chosen_hits = sum(1 for kw in keywords if kw in chosen_text)

        # Só dispara se chosen=0 hits (verdadeira alucinação)
        if chosen_hits > min_chosen_misses:
            fixed.append(m)
            continue

        # Procura melhor candidato — score precisa ser FORTE
        best_i = -1
        best_score = -1
        for i, ctext in enumerate(cue_lower):
            score = sum(1 for kw in keywords if kw in ctext)
            if score > best_score:
                best_score = score
                best_i = i

        # Substitui só se gap é GRANDE (>= min_score_gap)
        if (
            best_score >= chosen_hits + min_score_gap
            and best_i >= 0
            and (best_i + 1) != cue_id
        ):
            m = dict(m)
            m["cue_id"] = best_i + 1
            m["why"] = f"[validado] {why}"
        fixed.append(m)

    return fixed


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
    enforce_monotonicity: bool = False,  # desligado por default: roteiros de short costumam ser non-linear (hook no clímax → flashback → volta pra conclusão). O monotonic destruía isso empurrando beats "retrocedendo" pra perto do anterior, colocando 14 beats consecutivos na mesma cena. Deixar a LLM decidir — ela ACERTA o non-linear.
    max_backward_seconds: float = 15.0,
    ad_cues: Optional[List[Cue]] = None,
    visual_glossary: Optional[dict] = None,
    audio_envelope=None,
    diversity_min_gap: float = 8.0,
) -> List[SceneMatch]:
    """Monta prompt com cues agrupadas, chama LLM, valida cobertura, aplica
    snap bidirecional. Beats sem match da LLM herdam a cena do beat anterior
    (melhor que fallback proporcional pra continuidade visual).

    `ad_cues` (opcional): cues vindos de Audio Description (Whisper do áudio
    descritivo). Quando fornecidos, entram no prompt como descrição visual
    marcada com `[VISUAL]`, enriquecendo a decisão do matcher. Não substitui
    as cues de diálogo — apenas complementa.
    """
    if not beats:
        return []

    # AD e diálogo mesclados cronologicamente — ambos viram cue_id selecionável.
    # Isso permite beats de AÇÃO VISUAL casarem com o timestamp exato do AD.
    grouped_dialog = group_cues(cues, max_gap=group_cues_gap)
    # IMPORTANTE: AD cues NÃO são agrupadas. Cada descrição AD do Gemini é
    # uma "ação visual atômica" (ex: "Uma grande chama irrompe" em 980s,
    # "Coco arregala os olhos" em 1037s). Agrupar funde 30+ descrições em
    # 1 bloco gigante e o LLM perde a granularidade — não consegue mais
    # apontar pro momento exato. Sem agrupamento, cada AD vira sua linha
    # CUE=N com texto curto e específico, fácil de selecionar.
    if ad_cues:
        grouped, ad_index_set = _merge_subtitle_and_ad(grouped_dialog, list(ad_cues))
    else:
        grouped = grouped_dialog
        ad_index_set = set()

    # Pseudo-cues de SILÊNCIO: animes sem AD têm cenas silenciosas
    # relevantes (thought bubbles, transições visuais, reflexão sem fala).
    # Sem essas pseudo-cues, o matcher não enxerga esses momentos e força
    # beats descritivos pra cair em cues de fala próximas (que mostram outra
    # coisa visualmente). Ativadas só quando NÃO há AD — quando há AD, ele
    # já cobre cenas silenciosas com descrição visual real.
    silence_index_set: set = set()
    if not ad_cues and scene_changes:
        grouped, silence_index_set = inject_silence_cues(
            grouped, scene_changes,
            min_silence_gap=5.0,
            min_silence_window=1.5,
        )

    # Glossário visual: mapeia termos do roteiro → aliases do AD. Reduz
    # alucinação do matcher (ex: "feiticeiro mascarado" no roteiro vs.
    # "vendedor de livros com olho grande" no AD).
    from core.visual_index import render_glossary_for_prompt
    glossary_text = render_glossary_for_prompt(visual_glossary or {})
    visual_glossary_section = (
        glossary_text if glossary_text else "(sem glossário visual)"
    )

    # Beat archetypes — direciona o matcher a aplicar regras diferentes
    # conforme HOOK, SETUP, ESCALADA, CLIMAX, PAYOFF.
    from core.beat_archetypes import classify_beats, render_archetypes_for_prompt
    archetypes = classify_beats(beats)
    archetypes_text = render_archetypes_for_prompt(archetypes)
    archetypes_section = archetypes_text if archetypes_text else "(arquétipos não classificados)"

    prompt = MATCHER_PROMPT.format(
        summary=(summary or "(sem resumo disponível)").strip(),
        visual_glossary_section=visual_glossary_section,
        archetypes_section=archetypes_section,
        cues_table=_cues_table(
            grouped,
            ad_indices=ad_index_set,
            silence_indices=silence_index_set,
            audio_envelope=audio_envelope,
        ),
        visual_hints_block="",  # obsoleto — AD agora é selecionável na tabela
        beats_table=_beats_table(beats),
        n_beats=len(beats),
    )

    # Dump do prompt em %TEMP%\ancopy\work\ pra debug. Permite inspeção do que
    # a LLM recebeu quando a seleção sai estranha. Não afeta comportamento.
    try:
        import tempfile as _tmp
        debug_dir = os.path.join(_tmp.gettempdir(), "ancopy", "work")
        os.makedirs(debug_dir, exist_ok=True)
        with open(os.path.join(debug_dir, "last_matcher_prompt.txt"), "w", encoding="utf-8") as f:
            f.write(prompt)
    except Exception:
        pass

    raw = navy.chat_completion(
        api_key=api_key,
        base_url=base_url,
        model=model,
        messages=[{"role": "user", "content": prompt}],
        timeout=240.0,
        # temperature=0.3: meio termo entre determinismo (que matava
        # criatividade visual) e variação livre (que escolhia hooks errados
        # entre runs idênticas). 0.3 mantém o LLM ousando em escolhas
        # visuais mas sem cair em alucinações inconsistentes.
        temperature=0.3,
    )

    # Dump da resposta crua pra cross-reference com o prompt
    try:
        import tempfile as _tmp
        debug_dir = os.path.join(_tmp.gettempdir(), "ancopy", "work")
        with open(os.path.join(debug_dir, "last_matcher_response.txt"), "w", encoding="utf-8") as f:
            f.write(raw or "(resposta vazia)")
    except Exception:
        pass

    body = _extract_json_block(raw)
    raw_matches = _parse_matches_resilient(body)

    # NOTA: validação semântica DESABILITADA. A intuição era boa (corrigir
    # quando LLM diz why correto mas devolve cue_id errado), mas na prática
    # ela movia BEATS CERTOS pra cenas erradas mais vezes do que corrigia
    # alucinações reais. Como o sanity check de cue_id (timestamp vs índice)
    # já recupera o caso mais comum, a validação semântica vira ruído.
    # Mantida no código pra possível uso futuro com algoritmo melhor.
    # raw_matches = _validate_matches_semantically(raw_matches, grouped)

    by_beat = {int(m.get("beat")): m for m in raw_matches if m.get("beat") is not None}

    missing = [b.index for b in beats if b.index not in by_beat]

    scene_changes = scene_changes or []
    results: List[SceneMatch] = []
    for b in beats:
        m = by_beat.get(b.index)
        cue = None
        why = ""
        confidence = 0
        runner_up_start = -1.0
        if m is not None:
            # Confidence 1-5 do LLM (0 = não informado / parse falhou)
            try:
                confidence = max(0, min(5, int(m.get("confidence") or 0)))
            except (ValueError, TypeError):
                confidence = 0
            cue_id = int(m.get("cue_id") or 0)
            if 1 <= cue_id <= len(grouped):
                cue = grouped[cue_id - 1]
            elif cue_id > len(grouped):
                # SANITY CHECK: LLM às vezes confunde cue_id com o timestamp
                # em segundos da linha (ex: devolve 1133 ao ver CUE=109 em
                # 1133.88s). Quando o número está muito acima do range de
                # índices válidos mas parece um timestamp plausível, faz
                # busca reversa pela cue com start mais próximo.
                best_i = min(
                    range(len(grouped)),
                    key=lambda i: abs(grouped[i].start - cue_id),
                )
                if abs(grouped[best_i].start - cue_id) < 30.0:
                    cue = grouped[best_i]
                    # Marca no why pra debug
                    cue_id = best_i + 1
            why = str(m.get("why") or "").strip()

            # Runner-up: 2ª melhor cue do LLM — vira candidato "opção B"
            # no editor de plano. Só aceita id válido e diferente da escolhida.
            try:
                ru = m.get("runner_up_cue_id")
                ru = int(ru) if ru is not None else 0
            except (ValueError, TypeError):
                ru = 0
            if 1 <= ru <= len(grouped) and grouped[ru - 1] is not cue:
                runner_up_start = grouped[ru - 1].start

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

        # find_clean_window avalia múltiplos scene changes em volta do cue
        # e escolhe o que MINIMIZA flashes (sub-clips < 1s) dentro da
        # duração do beat. Mais robusto que snap_to_scene puro, que só
        # puxava pra cena adjacente sem checar o que vinha depois.
        if cue and scene_changes:
            video_start = find_clean_window(
                cue_start=cue.start,
                cue_end=cue.end,
                beat_duration=b.duration,
                scenes=scene_changes,
                proximity=4.0,
                min_clip_duration=1.0,
            )
        else:
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
            confidence=confidence, runner_up_start=runner_up_start,
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

    # Monotonicidade temporal: beats consecutivos não podem retroceder muito.
    # Resolve casos onde o LLM escolhe uma cue anterior pra um beat de
    # fechamento (ex: beat 26 "será que elas vão sair dessa?" voltando 90s).
    if enforce_monotonicity:
        _enforce_temporal_monotonicity(
            results, cues, scene_changes,
            max_backward_seconds=max_backward_seconds,
        )

    # Diversity guard: evita 2 beats consecutivos caírem em timestamps
    # próximos (mesmo "shot" do anime). Editor humano nunca faz isso —
    # alterna ângulos. Se beat N e N+1 estão a < diversity_min_gap segs,
    # tenta deslocar o N+1 pra próxima scene change na janela disponível.
    if scene_changes:
        _enforce_cross_beat_diversity(
            results, scene_changes,
            min_gap=diversity_min_gap,
        )

    # Safety net anti-landscape: verifica se cada video_start tem rosto; se
    # não, tenta mover pra próxima scene change dentro da janela.
    if avoid_landscape and mkv_path and os.path.isfile(mkv_path):
        _avoid_landscape_pass(
            results, mkv_path, scene_changes, landscape_search_seconds,
        )

    return results


def _enforce_cross_beat_diversity(
    results: List[SceneMatch],
    scene_changes: List[float],
    min_gap: float = 8.0,
) -> int:
    """Garante que beats consecutivos não fiquem na mesma "cena visual".

    Se beat N+1 está a menos de `min_gap` segundos do beat N (mesmo "shot"),
    desloca o N+1 pra próxima scene change disponível dentro de uma janela
    razoável (até 15s pra frente). Editor profissional alterna ângulos —
    sem isso, é comum ver 3 closeups consecutivos da mesma personagem.

    Não atua quando os beats estão em timestamps muito distantes (saltos
    temporais legítimos do roteiro non-linear).
    """
    moved = 0
    if not results or not scene_changes:
        return 0

    scenes_sorted = sorted(scene_changes)
    for i in range(1, len(results)):
        prev = results[i - 1]
        cur = results[i]
        gap = cur.video_start - prev.video_start

        # Só atua quando há OVERLAP/PROXIMIDADE TEMPORAL (não em saltos
        # narrativos legítimos, ex: beat 5 em 1280s, beat 6 em 50s)
        if 0 < gap < min_gap:
            # Tenta deslocar pra próxima scene change após prev.video_end
            target_after = prev.video_start + prev.beat.duration + 0.2
            candidates = [
                s for s in scenes_sorted
                if target_after <= s <= cur.video_start + 15.0
                and s > cur.video_start  # de fato avança
            ]
            if candidates:
                new_start = candidates[0]
                cur.video_start = new_start
                cur.video_end = new_start + cur.beat.duration
                cur.snapped = True
                cur.why = f"[diversity] {cur.why}"
                moved += 1
    return moved


def _enforce_temporal_monotonicity(
    results: List[SceneMatch],
    cues: List[Cue],
    scene_changes: Optional[List[float]],
    max_backward_seconds: float = 15.0,
    lookback_window: int = 4,
) -> int:
    """Empurra beats que retrocederam demais pra frente no timeline.

    Usa MEDIANA dos últimos `lookback_window` beats (não só o anterior)
    como referência de "tempo atual". Isso previne outliers de contaminar
    toda a sequência: se beat 1 foi parar num timestamp errado, os beats
    seguintes ainda têm chance de ficar na região correta porque a mediana
    ignora o outlier.

    Regra:
    - floor = median(últimos N video_starts) - max_backward_seconds
    - Se video_start[i] < floor, consideramos violação e empurramos pra
      frente achando a próxima cue com start >= floor.

    Retorna quantos beats foram movidos.
    """
    if not results or len(results) < 2:
        return 0

    scene_changes = scene_changes or []
    moved = 0
    cues_sorted = sorted(cues, key=lambda c: c.start)

    def _median(xs):
        xs = sorted(xs)
        n = len(xs)
        if n == 0:
            return 0.0
        return xs[n // 2] if n % 2 == 1 else (xs[n // 2 - 1] + xs[n // 2]) / 2

    for i in range(1, len(results)):
        cur = results[i]
        window = results[max(0, i - lookback_window):i]
        ref = _median([r.video_start for r in window])
        floor = ref - max_backward_seconds

        if cur.video_start >= floor:
            continue  # OK

        # Candidato 1: próxima cue com start >= floor
        target_start = None
        for c in cues_sorted:
            if c.start >= floor:
                target_start = c.start
                break

        # Se não achar ou o candidato está muito longe (>60s), usa o floor direto
        if target_start is None or target_start > ref + 60.0:
            target_start = floor

        # Snap pra scene change mais próxima (pequena tolerância pra cada lado)
        snapped_start = snap_to_scene(
            target_start, scene_changes,
            max_backward=2.0, max_forward=2.0,
        )

        old_start = cur.video_start
        cur.video_start = max(0.0, snapped_start)
        cur.video_end = cur.video_start + cur.beat.duration
        cur.snapped = True
        cur.why = (
            f"[monotonic: {old_start:.1f}s→{cur.video_start:.1f}s] {cur.why}"
        )
        moved += 1

    return moved


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
