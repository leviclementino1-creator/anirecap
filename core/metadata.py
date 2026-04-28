"""Geração de títulos + descrição pra YouTube/TikTok/Reels a partir do roteiro.

Recebe o short_script e produz JSON com:
- 5 sugestões de título (curtos, ~50-70 chars, com hook + emoji)
- 1 descrição (3-5 frases + #hashtags)

O LLM (Gemini via Navy) roda na temperatura mais alta (0.8) pra variedade de
títulos — diferente do summary/short_script que são fiéis aos fatos.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import List

from providers import navy


METADATA_PROMPT_VERSION = "metadata-v1-2026-04-28"


_PROMPT = """\
Você é um especialista em copywriting pra shorts virais (YouTube Shorts,
TikTok, Reels) de animes. Recebe abaixo o ROTEIRO de narração de um short
de ~60-90s. Sua tarefa:

1. Gerar 5 sugestões de TÍTULO em português, curtinhos (40-65 caracteres
   incluindo emoji), que façam o usuário PARAR DE SCROLLAR. Cada título
   deve:
   - Provocar curiosidade (hook), revelar conflito, ou prometer algo absurdo
   - Usar 1-2 emojis no MÁXIMO (não polui)
   - Mencionar o nome do anime SE ele já é reconhecido (Witch Hat Atelier,
     Dr. Stone). Caso contrário, foco no drama interno.
   - Variar o estilo: pergunta, afirmação chocante, "POV:", ranking
   - NÃO use clickbait mentiroso (não inventa fato que o roteiro não diz)
   - NÃO use ALL CAPS no título inteiro (só em palavras pontuais pra ênfase)

2. Gerar 1 DESCRIÇÃO em português, 2-4 frases, neutra-engajada, terminando
   com 4-7 hashtags relevantes (#anime, #nomedoanime, #witchhat etc).

REGRAS:
- Tudo factualmente fiel ao ROTEIRO. Não invente eventos.
- Português brasileiro coloquial (mesmo tom do roteiro).
- A descrição PODE conter spoiler leve (já tá no roteiro), mas sem revelar
  o final por completo. Pode terminar com pergunta retórica.

SAÍDA: APENAS JSON válido, sem markdown, sem prefácio. Comece direto com
`{`. Formato:
{
  "titles": [
    "Título 1 com emoji",
    "Título 2",
    "Título 3",
    "Título 4",
    "Título 5"
  ],
  "description": "Texto da descrição. Pode ter 2-4 frases. #hashtag1 #hashtag2 #hashtag3 #hashtag4"
}

ROTEIRO:
"""


@dataclass
class ShortMetadata:
    titles: List[str]
    description: str


def _strip_json_fences(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        raw = "\n".join(lines).strip()
    return raw


def generate_metadata(
    short_script: str,
    api_key: str,
    base_url: str,
    model: str = "gemini-2.5-flash",
    timeout: float = 60.0,
) -> ShortMetadata:
    """Pede ao LLM 5 títulos + 1 descrição pro short.

    Lança RuntimeError se a saída não for JSON válido com as chaves esperadas.
    """
    if not short_script or not short_script.strip():
        raise ValueError("short_script vazio — gere o roteiro antes")

    content = _PROMPT + short_script.strip()
    raw = navy.chat_completion(
        api_key=api_key,
        base_url=base_url,
        model=model,
        messages=[{"role": "user", "content": content}],
        timeout=timeout,
        temperature=0.8,  # variedade nos títulos
    )

    raw = _strip_json_fences(raw or "")
    if not raw:
        raise RuntimeError("LLM retornou resposta vazia")

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"LLM não devolveu JSON válido: {e}\nRaw: {raw[:300]}")

    titles = data.get("titles") or []
    description = (data.get("description") or "").strip()

    if not isinstance(titles, list) or len(titles) < 1:
        raise RuntimeError("LLM não devolveu lista de títulos")
    if not description:
        raise RuntimeError("LLM não devolveu descrição")

    # Sanitiza títulos (remove aspas externas, espaços extras)
    cleaned: List[str] = []
    for t in titles:
        if not isinstance(t, str):
            continue
        t = t.strip().strip('"\'').strip()
        t = re.sub(r"\s+", " ", t)
        if t:
            cleaned.append(t)

    if not cleaned:
        raise RuntimeError("Todos os títulos do LLM ficaram vazios após limpeza")

    return ShortMetadata(titles=cleaned[:5], description=description)
