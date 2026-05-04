"""Geração de texto via LLM em duas etapas:

1. `generate_summary_stream`  — transcrição → resumo detalhado (neutro, 300-500 palavras)
2. `generate_short_script_stream` — resumo → roteiro de narração curta (60-90s, ~180 palavras)

O TTS do ElevenLabs deve consumir **apenas** o short_script, nunca o resumo.
"""
from typing import Iterator

from providers import navy


# Versões semânticas dos prompts — servem como cache key em vez do prompt
# literal. Bumpar SEMPRE que a mudança no prompt deve invalidar resultados
# anteriores (ex: nova regra, filosofia diferente). Edits cosméticos (typo,
# reword, reorganização) NÃO precisam bumpar.
SUMMARY_PROMPT_VERSION = "summary-v5-atribuicao-acoes-2026-04-28"
SHORT_SCRIPT_PROMPT_VERSION = "short-v5-pronomes-honorificos-2026-04-28"


SUMMARY_PROMPT = """\
Você é um analista de animes. Leia a transcrição abaixo e escreva um resumo
detalhado do episódio em português.

OBJETIVO:
Entender completamente o que aconteceu — personagens, relações, eventos em
ordem, reviravoltas, momentos emocionais — de forma que esse resumo sirva
depois como base para escrever um roteiro curto de short.

REGRAS:
- Texto corrido, sem bullets, sem markdown, sem títulos.
- Ordem cronológica.
- Inclua nomes dos personagens principais e as relações entre eles.
- Destaque plot twists, romance, comédia, tensão, revelações.
- Preserve detalhes que permitam recontar a história com graça depois.
- Linguagem clara e descritiva — NÃO estilo viral, NÃO estilo shorts.
- Tamanho alvo: 300 a 500 palavras.

REGRAS DE FIDELIDADE FACTUAL (CRÍTICO):
- NÃO INVENTE descrições físicas de ações que não estão claramente
  no transcript. Se um personagem usa "som como arma", descreva como
  "ataca com ondas sonoras" — NÃO escreva "carregou a vítima pelas
  orelhas" se isso não está literalmente no transcript.
- Se uma habilidade é abstrata/metafórica (som, magia, telepatia),
  preserve a abstração. Não force interpretação física literal.
- DESAMBIGUE PRONOMES quando há mais de um sujeito feminino/masculino
  na sentença. Em vez de "sua irmã mais velha" use "a irmã mais velha
  DELE/DELA" ou repita o nome ("a irmã mais velha do Mitsuhiko").
  Exemplo: "Mitsuhiko contratou Gero para proteger Shiori de Futae,
  irmã mais velha do Mitsuhiko" (NÃO "irmã mais velha de Shiori" se
  Futae é tia da Shiori).
- Quando o relacionamento entre personagens estiver pouco claro no
  transcript, OMITA em vez de inventar (não diga "irmã" sem ter
  certeza; diga "Futae, que quer a empresa" sem rotular relação).
- Para CADA afirmação que você escreve, deveria conseguir apontar
  uma linha do transcript que sustenta. Na dúvida, OMITA.

ATRIBUIÇÃO DE AÇÕES — REGRA CRÍTICA (cuidado redobrado):
Quando uma cena tem múltiplos personagens e a legenda mostra uma
AÇÃO FÍSICA (chute, soco, queda, abraço, beijo, empurrão) sem
identificar claramente quem é o AGENTE, NÃO ESCOLHA O PERSONAGEM
EM FOCO da cena anterior. Em vez disso:
  - Use voz passiva: "Yuu leva um chute no queixo"
  - Use sujeito genérico: "Alguém chuta Yuu na confusão"
  - Ou OMITA o detalhe se a legenda não nomeia o agente

Erros comuns a EVITAR:
- "Motoko bate em Yuu" quando a legenda só tem "[chute]" sem nome
  (o agente real pode ser outro personagem que entrou na cena)
- Assumir que quem FALA é quem AGE — fala e ação são entidades
  diferentes
- Inferir agente quando o transcript tem só som ("[soco]", "[grito]")
  ou descritor passivo ("ele cai", "ela é atingida")

NÃO ESCALE ACÕES (não force interpretação mais forte que o transcript):
- "ela sugere"     ≠ "ela convence" / "ela manipula"
- "ele aceita"      ≠ "ele cede" / "ele é forçado"
- "ele comenta que viu antes" ≠ "ele examina de novo"
- "ele menciona"   ≠ "ele revela" (a menos que seja revelação de fato)
- "ele observa"    ≠ "ele descobre"

Verbos no transcript devem ser PRESERVADOS no resumo, não amplificados.

NÃO INVENTE EVENTOS NÃO VISTOS:
Se o transcript mostra "X analisou Y antes" via diálogo (flashback verbal),
NÃO escreva como se X analisasse Y de novo no presente. Distinga:
  - AÇÃO ATUAL no transcript: "examina o pé"
  - REFERÊNCIA a ação passada: "comenta o que viu da outra vez"
Não converta referência verbal em ação narrativa nova.

NOMES DOS PERSONAGENS:
- Remova HONORÍFICOS japoneses do nome (-kun, -chan, -san, -sama,
  -senpai, -sensei). "Miku-senpai" no transcript vira "Miku" no
  resumo. "Otaku-kun" vira "Otaku". Honorífico não combina com
  narração pt-BR e atrapalha o tom do roteiro depois.
- EXCEÇÃO: preserve só se o honorífico é literalmente o NOME DO
  PERSONAGEM (ex: "Onee-sama" usado como apelido fixo).

TRANSCRIÇÃO:
"""


SHORT_SCRIPT_PROMPT = """\
Você é um roteirista de shorts virais de anime. Receba o resumo abaixo e
REESCREVA como um roteiro de narração curta, 60-90 segundos (~160-220 palavras
em português FALADO).

═══════════════════════════════════════════════════════════════════════
 PROCESSO OBRIGATÓRIO — siga NA ORDEM antes de escrever uma palavra:
═══════════════════════════════════════════════════════════════════════

PASSO 1. Leia o resumo inteiro. Liste mentalmente os momentos dramáticos.

PASSO 2. Escolha O HOOK seguindo esta HIERARQUIA ESTRITA (pare na 1ª que
         tiver disponível — não desça pra níveis menores se há maior):

  NÍVEL 1 — ACUSAÇÃO/REVELAÇÃO pesada sobre o passado de um personagem
            ("ela foi acusada de petrificar a própria mãe", "descobriu que
             o pai é o vilão", "ele é o irmão perdido que todos achavam morto")
  NÍVEL 2 — TWIST de identidade, lealdade ou motivação
            ("o mestre era o traidor o tempo todo", "ela se apaixonou pelo
             inimigo sem saber")
  NÍVEL 3 — MORTE, TRAIÇÃO ou SACRIFÍCIO de personagem relevante
  NÍVEL 4 — ABSURDO COMÉDICO/SITUACIONAL impossível de ignorar
            ("esse otaku saiu com 3 garotas ao mesmo tempo", "eles vão fazer
             um foguete de bambu pra ir pra Lua")
  NÍVEL 5 — EVENTO DE IMPACTO VISUAL (dragão aparece, cidade desaparece,
            teletransporte, explosão). SÓ use se níveis 1-4 não existem.
  NÍVEL 6 — Evento comum (alguém viajou, praticou, aprendeu). NUNCA usar
            se há níveis 1-5. Essa é a preguiça. Evite.

PASSO 3. AUTO-CHECK (interno — NÃO ESCREVA NO OUTPUT):
         Valide que o hook escolhido é do nível mais alto possível. Se o
         nível é 5-6 E existe algo que caberia nos níveis 1-4, reescolha.
         ⚠️  NÃO IMPRIMA "HOOK ESCOLHIDO:" nem "NÍVEL:" nem nenhum meta-texto
             explicando sua escolha. Essa validação é apenas raciocínio
             interno — não aparece no resultado final.

PASSO 4. Estruture o roteiro NON-LINEAR:
         [HOOK] — 1-2 frases soltando o momento-clímax escolhido.
         [SETUP] "Tudo começou com..." / "Olha só..." — flashback breve.
         [DESENVOLVIMENTO] complicação, escalada, 2-3 blocos.
         [VOLTA AO CLÍMAX] retoma o hook com contexto, reforçando impacto.
         [FECHAMENTO] gancho, pergunta retórica, "e agora?".

═══════════════════════════════════════════════════════════════════════
 EXEMPLOS CONTRASTIVOS — aprenda com eles:
═══════════════════════════════════════════════════════════════════════

RESUMO HIPOTÉTICO (pra ambos exemplos): Coco, aprendiz, pratica magia e
se queima. Conserta sapatos da Agathe. Vão a Carn comprar caneta. Coco
vê feiticeiro mascarado (mesmo que deu livro proibido), corre atrás. As
meninas são teletransportadas pra lugar estranho, dragão aparece, feiticeira
diz "Coco é a única esperança". Tentam fugir, Coco atrapalha Agathe que
EXPLODE e acusa Coco de ter petrificado a própria mãe com magia proibida.
Confraria do Capuz está envolvida.

❌ HOOK RUIM (nível 5 — não use se há nível 1):
   "Mano, imagina: você tá lá na primeira loja de magia, se perdendo, e do
    nada é teletransportada com um dragão gigante!"
   POR QUE É RUIM: Escolheu evento de impacto visual (nível 5) quando o
   resumo TEM uma acusação pesada (nível 1: "petrificou a própria mãe").
   Teletransporte + dragão é cool mas EQUALIZA — qualquer isekai tem. A
   acusação é ÚNICA desse ep e muito mais poderosa.

✅ HOOK BOM (nível 1 — extraiu o melhor):
   "Mano, essa garota acabou de ser acusada de petrificar a própria mãe,
    e a culpada ainda é a magia que ela nem sabia que era proibida."
   POR QUE É BOM: Pegou a REVELAÇÃO pesada do clímax. Chocante, única,
   pessoal. Cria pergunta imediata na cabeça do espectador ("como assim
   petrificou a mãe?"). Daí em diante você CONTA como chegou nesse ponto.

ROTEIRO INTEIRO IDEAL (siga esse padrão de estrutura + voz):

> Mano, essa garota acabou de ser acusada de petrificar a própria mãe, e
> a culpada ainda é a magia que ela nem sabia que era proibida.
>
> Tudo começou com a Coco, aprendiz de feiticeiro, tentando dominar a
> magia. Ela é tipo super desastrada, tá ligado? Quase incendiou tudo
> tentando levitar uma bola de fogo! Aí, pra consertar uns sapatos
> voadores da Agathe, a colega dela, a Coco precisa ir numa loja mágica
> na cidade flutuante de Carn.
>
> Chegando lá, a cidade é surreal, toda mágica. Mas aí, a Coco vê o
> feiticeiro mascarado que deu a ela o livro de feitiços proibidos. Ela,
> toda impulsiva, sai correndo atrás dele e se perde das amigas.
>
> Mas aí o plot twist: elas acabam num beco, e a cidade simplesmente
> MUDA. Fica toda branca, elas percebem que não estão mais em Carn.
> Teletransporte proibido! E pra piorar, uma feiticeira sinistra aparece,
> falando que a Coco é a "única esperança" antes que um dragão surja.
>
> E o dragão aparece mesmo! A Agathe tenta uma magia de fogo, mas a Coco
> atrapalha. E é aí que a Agathe explode: chama a Coco de peso morto e
> joga na cara que foi por causa da MAGIA DA COCO que a MÃE DA COCO virou
> pedra. Caraca!
>
> Enquanto isso, o mestre Kieffrey descobre que a Confraria do Capuz,
> uns magos que fazem magia proibida, tão atrás da Coco. E agora, como
> elas vão sair dessa?

Note a ESTRUTURA: hook-acusação (nv1) → flashback setup → desenvolvimento
→ volta ao clímax (retomada da acusação) → gancho final.

═══════════════════════════════════════════════════════════════════════
 REGRA ZERO — FIDELIDADE FACTUAL (CRÍTICO, não negociável):
═══════════════════════════════════════════════════════════════════════
Tudo que você afirmar TEM que estar no resumo. NÃO EXAGERE, NÃO TROQUE
VÍTIMAS, NÃO INVENTE AÇÕES.
- "Coco queimou a si mesma" ≠ "Coco jogou fogo na amiga".
- "Agathe gritou com Coco" ≠ "Agathe bateu na Coco".
- "Tetia viu um dragão" ≠ "o dragão matou a Tetia".
- "petrificou a própria mãe (da Coco)" ≠ "petrificou a mãe da Agathe".
  → CUIDADO COM PRONOMES AMBÍGUOS. "Agathe acusa Coco de petrificar a mãe
    DELA" — esse "dela" é MÃE DA COCO (a feiticeira que recebeu a magia
    proibida foi Coco, então é a mãe DELA que virou pedra). Quando escrever
    o roteiro, NUNCA use "mãe dela" ambíguo: escreva "mãe da Coco" sempre
    que a vítima for ela mesma.
Pode DRAMATIZAR o TOM ("mano", "tipo", "e o pior"), NUNCA dramatize os
FATOS. Na dúvida, omita o detalhe.

HOOK — ATENÇÃO:
- NÃO use "Chat, acabei de assistir...", "Eu vi esse ep...", "Gente, deixa
  eu contar...", nem nenhuma variação de abertura-de-reação. Essas aberturas
  estão BANIDAS.
- A primeira frase já joga o espectador dentro do conflito, absurdo ou
  twist principal. Pode ser uma imagem chocante, uma revelação direta, uma
  pergunta provocativa.
- Exemplos de bons hooks:
  * "Mano... esse otaku saiu com DUAS garotas ao mesmo tempo."
  * "Imagina ser o cara mais perigoso do submundo e virar uma criança."
  * "Ela descobriu que o noivo é, literalmente, o vilão do reino."
  * "Esse cara beijou ela no meio da rua e fez o inferno virar."

ESTRUTURA (interna — NÃO imprima os rótulos):
- HOOK: 1 frase direta ao conflito/absurdo.
- BUILD 1, 2, 3: blocos curtos, cada um escalando a história.
- ESCALADA → MOMENTO-CHAVE → TWIST/PAYOFF.
- FECHAMENTO: deixa tensão, pergunta retórica ou gancho pro próximo ep.

VOZ FALADA — CRÍTICO:
- Frases curtas. Nada de frases longas e descritivas.
- Linguagem de fala, não de livro. Use "aí", "mano", "só que", "tipo",
  "olha só", "e o pior", "cara", "tá ligado".
- Conectivos rápidos entre blocos ("então", "mas aí", "enfim").
- Vocabulário otaku moderno quando couber, SEM FORÇAR: "toda bobinha",
  "plot twist", "tá gamada", "beta", "otaku raiz".
- NÃO use expressões regionais obscuras ou datadas:
  * "entrega o ouro"   → use "conta tudo" / "revela" / "solta"
  * "bagunçar o coreto" → use "causar confusão" / "complicar tudo"
  * "farmando aura"    → use só se o contexto é claríssimo, senão corta
  * Qualquer gíria que precise explicação está PROIBIDA.
- Nada de markdown, hashtags (#), asteriscos (*), títulos, bullets.

PRONOMES E REPETIÇÃO DE NOMES:
- Use o nome do personagem na PRIMEIRA menção em cada bloco. Depois,
  prefira pronomes (ele, ela, eles, elas) ou referências curtas
  ("o cara", "a mina", "a moleca", "o garoto") pra não soar robótico.
- Repetir "Yukiya" 8 vezes seguidas é ruim. Mas trocar de "Yukiya" pra
  "ele" no MEIO de uma frase com 2 personagens do mesmo gênero gera
  ambiguidade — nesse caso, repete o nome.
- Exemplo BOM:
  > "O Yukiya tava bolado. Ele decide se esforçar pra um encontro com
  >  a Miku. Ela já tava planejando o look, e ele, surtado, alugou
  >  vestido pra ela."
- Exemplo RUIM (repete demais):
  > "O Yukiya tava bolado. O Yukiya decide se esforçar pra um encontro
  >  com a Miku. A Miku já tava planejando, e o Yukiya alugou vestido
  >  pra Miku."

HONORÍFICOS JAPONESES — REMOVA SEMPRE:
- Tira -kun, -chan, -san, -sama, -senpai, -sensei dos nomes.
- "Otaku-kun" → "Otaku". "Miku-senpai" → "Miku". "Sensei Aizawa" →
  "Aizawa". Honorífico não traduz pro PT brasileiro coloquial e
  soa esquisito na narração.
- EXCEÇÃO: se o personagem é literalmente CHAMADO assim no roteiro
  como APELIDO/IDENTIDADE (ex: "Onee-sama" como nome próprio numa
  série), aí preserva. Mas isso é raro.

CORTE IMPIEDOSO:
- Remova detalhes secundários.
- Preserve só o que gera curiosidade, tensão, humor, choque ou romance.
- Se um personagem não é crítico pro hook ou pro payoff, corta.

SAÍDA: APENAS o texto corrido da narração em português, pronto pra TTS ler
em voz alta. A PRIMEIRA PALAVRA da saída é a PRIMEIRA palavra do hook
(tipicamente "Mano," ou direto no conflito). NÃO comece com:
- "HOOK ESCOLHIDO:" ou "NÍVEL:" (isso é raciocínio interno, não sai)
- "Aqui está o roteiro:" ou "Segue o roteiro:" (filler conversacional)
- "Com base no resumo..." (preâmbulo desnecessário)
- Qualquer cabeçalho, markdown, label ou explicação prévia.
Comece a saída direto pelo texto narrado. Fim.

RESUMO DO EPISÓDIO:
"""


def _stream(api_key: str, base_url: str, model: str, user_content: str) -> Iterator[str]:
    messages = [{"role": "user", "content": user_content}]
    yield from navy.chat_completion_stream(
        api_key=api_key, base_url=base_url, model=model, messages=messages,
    )


def _nonstream(api_key: str, base_url: str, model: str, user_content: str) -> str:
    messages = [{"role": "user", "content": user_content}]
    return navy.chat_completion(
        api_key=api_key, base_url=base_url, model=model, messages=messages,
    )


def generate_summary_stream(
    api_key: str, base_url: str, model: str, transcript: str,
) -> Iterator[str]:
    content = f"{SUMMARY_PROMPT}\n{transcript}"
    yield from _stream(api_key, base_url, model, content)


def generate_summary(
    api_key: str, base_url: str, model: str, transcript: str,
) -> str:
    content = f"{SUMMARY_PROMPT}\n{transcript}"
    return _nonstream(api_key, base_url, model, content)


def generate_short_script_stream(
    api_key: str, base_url: str, model: str, summary: str,
) -> Iterator[str]:
    content = f"{SHORT_SCRIPT_PROMPT}\n{summary}"
    yield from _stream(api_key, base_url, model, content)


def generate_short_script(
    api_key: str, base_url: str, model: str, summary: str,
) -> str:
    content = f"{SHORT_SCRIPT_PROMPT}\n{summary}"
    return _nonstream(api_key, base_url, model, content)
