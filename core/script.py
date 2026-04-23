"""Geração de texto via LLM em duas etapas:

1. `generate_summary_stream`  — transcrição → resumo detalhado (neutro, 300-500 palavras)
2. `generate_short_script_stream` — resumo → roteiro de narração curta (60-90s, ~180 palavras)

O TTS do ElevenLabs deve consumir **apenas** o short_script, nunca o resumo.
"""
from typing import Iterator

from providers import navy


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

TRANSCRIÇÃO:
"""


SHORT_SCRIPT_PROMPT = """\
Você é um roteirista de shorts virais de anime. Receba o resumo abaixo e
REESCREVA como um roteiro de narração curta, de 60 a 90 segundos
(aproximadamente 160 a 220 palavras em português FALADO).

REGRA DE OURO: isto é uma REESCRITA CRIATIVA, não uma compressão. Identifique
o momento mais chocante, absurdo, engraçado ou tenso do episódio e COMECE
DIRETO POR ELE. Reescreva o resto em blocos rápidos em torno desse hook.

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
- Vocabulário otaku quando couber ("farmando aura", "beta", "toda bobinha",
  "plot twist", "entrega o ouro", "tá gamada") — sem forçar.
- Nada de markdown, títulos, aspas envolvendo o todo, bullets.

CORTE IMPIEDOSO:
- Remova detalhes secundários.
- Preserve só o que gera curiosidade, tensão, humor, choque ou romance.
- Se um personagem não é crítico pro hook ou pro payoff, corta.

SAÍDA: APENAS o texto corrido da narração em português, pronto pra TTS ler
em voz alta.

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
