"""Constrói um GLOSSÁRIO VISUAL que conecta termos do roteiro/resumo com
descrições do AD (audio description).

Problema que resolve: o roteiro fala "feiticeiro mascarado que deu o livro
proibido", mas o AD desse personagem diz "O vendedor de livros da adega está
na janela. Seu rosto parece um grande olho." Sem esse mapeamento, o matcher
LLM não conecta os termos e escolhe cue errado.

Como funciona: faz UMA chamada LLM com (summary + script + amostra do AD) e
pede pro LLM identificar personagens/objetos chave e listar todas as formas
como aparecem nos textos. O glossário resultante é injetado no prompt do
matcher como contexto extra.

Cache: idempotente — mesmos inputs geram mesmo glossário; cacheado no disco.
"""
from __future__ import annotations

import json
import re
from typing import Dict, List, Optional

from core.cue import Cue
from providers import navy


VISUAL_INDEX_PROMPT_VERSION = "visualindex-v1-2026-04-28"


_PROMPT = """\
Você é um analista visual de animes. Recebe abaixo:
- O RESUMO do episódio
- O ROTEIRO do short que será narrado
- Uma AMOSTRA da AUDIO DESCRIPTION (descrição visual do que aparece na tela)

TAREFA: criar um GLOSSÁRIO VISUAL que conecta:
(1) personagens/objetos/elementos importantes mencionados no roteiro/resumo
(2) com as DIFERENTES formas como esses mesmos personagens/objetos aparecem
    descritos na audio description.

Por que isso importa: o roteiro pode chamar alguém de "feiticeiro mascarado"
mas a audio description pode descrevê-lo como "homem com rosto que parece
grande olho" ou "vendedor de livros". Quem ler o glossário precisa entender
que são a MESMA pessoa.

REGRAS:
1. Só inclua entradas onde o termo do roteiro aparece DESCRITO de forma
   diferente no AD. Se o AD usa o mesmo nome ("Coco" em ambos), NÃO inclua.
2. Cada chave do glossário é o nome canônico/curto (do roteiro/resumo).
3. O valor é uma lista das descrições alternativas vistas no AD.
4. Inclua personagens importantes, mas também OBJETOS ICÔNICOS ("livro
   proibido", "selo de conjuração", "sapatos voadores") se aparecem com
   nomes diferentes no AD.
5. NÃO INVENTE — só inclua mapeamentos que você consegue justificar lendo
   o resumo + AD.

SAÍDA: APENAS JSON válido. Sem markdown, sem prefácio. Comece com `{`.
Se não há aliases relevantes, devolve `{"glossary": {}}`.

Formato:
{
  "glossary": {
    "feiticeiro mascarado": ["vendedor de livros da adega", "homem com rosto que parece um grande olho"],
    "selo de conjuração": ["pavimento da viela com símbolos brancos", "símbolo desenhado com tinta preta"]
  }
}

═══════════════════════════════════════════════════════════════════════
 RESUMO:
═══════════════════════════════════════════════════════════════════════
{summary}

═══════════════════════════════════════════════════════════════════════
 ROTEIRO DO SHORT:
═══════════════════════════════════════════════════════════════════════
{script}

═══════════════════════════════════════════════════════════════════════
 AUDIO DESCRIPTION (amostra):
═══════════════════════════════════════════════════════════════════════
{ad_sample}
"""


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


def _ad_sample(ad_cues: List[Cue], max_lines: int = 60) -> str:
    """Reduz o AD a uma amostra representativa pra caber no prompt sem
    explodir tokens. Pega `max_lines` espaçados uniformemente."""
    if not ad_cues:
        return "(sem audio description disponível)"
    if len(ad_cues) <= max_lines:
        sample = ad_cues
    else:
        step = len(ad_cues) / max_lines
        sample = [ad_cues[int(i * step)] for i in range(max_lines)]
    return "\n".join(
        f"[{int(c.start // 60):02d}:{int(c.start % 60):02d}] {c.text}"
        for c in sample if c.text.strip()
    )


def build_visual_glossary(
    summary: str,
    short_script: str,
    ad_cues: List[Cue],
    api_key: str,
    base_url: str,
    model: str = "gemini-2.5-flash",
    timeout: float = 60.0,
) -> Dict[str, List[str]]:
    """Pede ao LLM um glossário {termo_roteiro: [aliases_no_AD]}.

    Retorna dict vazio em caso de erro ou se não há AD. NÃO levanta exception
    — falha silenciosa pra não bloquear o pipeline (matcher ainda funciona
    sem glossário, só com qualidade um pouco pior).
    """
    if not ad_cues:
        return {}
    if not summary.strip() or not short_script.strip():
        return {}

    prompt = (_PROMPT
              .replace("{summary}", summary.strip())
              .replace("{script}", short_script.strip())
              .replace("{ad_sample}", _ad_sample(ad_cues)))

    try:
        raw = navy.chat_completion(
            api_key=api_key, base_url=base_url, model=model,
            messages=[{"role": "user", "content": prompt}],
            timeout=timeout,
            temperature=0.2,  # baixa variância — quer mapeamento determinístico
        )
    except Exception:
        return {}

    raw = _strip_json_fences(raw or "")
    if not raw:
        return {}

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}

    g = data.get("glossary") or {}
    if not isinstance(g, dict):
        return {}

    # Normaliza: chaves str, valores list[str]
    out: Dict[str, List[str]] = {}
    for k, v in g.items():
        if not isinstance(k, str) or not isinstance(v, list):
            continue
        aliases = [str(a).strip() for a in v if isinstance(a, (str,)) and a.strip()]
        k = k.strip()
        if k and aliases:
            out[k] = aliases
    return out


def render_glossary_for_prompt(glossary: Dict[str, List[str]]) -> str:
    """Renderiza o glossário em texto pra injetar no prompt do matcher."""
    if not glossary:
        return ""
    lines = ["GLOSSÁRIO VISUAL — termo do roteiro = como aparece no AD:"]
    for k, aliases in glossary.items():
        joined = " | ".join(aliases)
        lines.append(f"  • {k}  =  {joined}")
    return "\n".join(lines)
