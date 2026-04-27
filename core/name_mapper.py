"""Mapeia nomes localizados (legenda FR/ES/etc) → nomes canônicos (AniList).

Releases diferentes usam transliterações diferentes do nome original japonês:
- Witch Hat Atelier (legenda FR Tsundere): "KIEFFREY"
- Witch Hat Atelier (AD FR): "Kifri"
- Witch Hat Atelier (oficial mangá EN): "Qifrey"

Pra normalizar, a gente:
1. Detecta nomes próprios distintos no transcript (regex + estatística)
2. Pega lista canônica do AniList
3. Pede pro Gemini fazer o mapping fonético cross-lingual
4. Aplica substituições no transcript
"""
from __future__ import annotations

import json
import re
from typing import Dict, List, Optional, Set

from core.anilist import CharacterInfo
from providers import navy


# Palavras curtas e comuns em PT/FR/EN que parecem nomes mas não são.
_NAME_BLACKLIST = {
    # PT/EN comuns
    "ela", "ele", "elas", "eles", "uma", "uns", "muito", "para", "quem",
    "como", "onde", "isso", "esse", "essa", "esta", "este", "todo", "nada",
    "mas", "que", "qual", "the", "and", "you", "are", "for", "with",
    # tags de speaker em CAPS
    "narrator", "narrador", "homem", "mulher",
    # FR comuns que aparecem em CAPS em ASS
    "oui", "non", "alors", "voilà", "merci",
    # title-cased generic
    "Coco",  # SEMPRE preserva (mesmo nome em todas as fontes)
}


# Palavras-tag que aparecem em CAPS dentro de subtitles ASS/SRT.
# Formato `[NOME]` é speaker indicator.
_SPEAKER_TAG_RE = re.compile(r"\[([A-ZÀ-Ý][A-ZÀ-Ý' \-]{1,30})\]")
# Nome próprio em texto (palavra com inicial maiúscula, no mínimo 3 chars).
# Pega "Coco", "Agathe", "Kieffrey", "Sr. Nornois" (parcialmente).
_PROPER_NOUN_RE = re.compile(r"\b([A-ZÀ-Ý][a-zà-ÿ]{2,})\b")


def detect_proper_nouns(
    text: str,
    min_occurrences: int = 2,
    max_names: int = 30,
) -> List[str]:
    """Extrai nomes próprios distintos de um texto.

    Combina dois sinais:
    1. Tags de speaker `[NAME]` (forte — quase certeza que é nome).
    2. Palavras com inicial maiúscula que aparecem N+ vezes (heurística —
       filtra começo-de-frase casuais).

    Retorna lista ordenada por frequência (mais frequente primeiro).
    """
    if not text:
        return []

    counts: Dict[str, int] = {}

    # Sinal forte: tags [NOME]
    for m in _SPEAKER_TAG_RE.finditer(text):
        raw = m.group(1).strip()
        if not raw:
            continue
        # Title-case pra normalizar (KIEFFREY → Kieffrey)
        norm = raw.title() if raw.isupper() else raw
        if norm.lower() in (b.lower() for b in _NAME_BLACKLIST):
            continue
        counts[norm] = counts.get(norm, 0) + 5  # peso 5 pra speaker tags

    # Sinal médio: nome próprio repetido
    for m in _PROPER_NOUN_RE.finditer(text):
        word = m.group(1)
        if word.lower() in (b.lower() for b in _NAME_BLACKLIST):
            continue
        counts[word] = counts.get(word, 0) + 1

    # Filtra por mínimo de ocorrências (filtra Title-case casual no início de frase)
    filtered = {w: c for w, c in counts.items() if c >= min_occurrences}

    # Ordena por frequência desc
    sorted_names = sorted(filtered, key=lambda w: -filtered[w])
    return sorted_names[:max_names]


_MAPPING_PROMPT = """\
Você recebe DUAS listas de nomes de personagens de um anime:

LISTA A — variações de localização que aparecem na legenda francesa do
episódio (transliterações imprecisas, podem estar em maiúsculas):
{detected}

LISTA B — nomes canônicos oficiais do AniList (banco de dados de anime):
{canonical}

TAREFA:
Pra cada nome em A, identifique qual nome em B é o mesmo personagem.
Use similaridade fonética (mesmo nome japonês original, transliterado de
formas diferentes). Considere que a legenda FR às vezes usa adaptações
muito diferentes (ex: 'KIEFFREY' = 'Qifrey', 'Agathe' = 'Agott',
'Nornois' = 'Nolnoa').

REGRAS:
- Se o nome em A não tem correspondência clara em B, mapeia ele pra ele
  mesmo (preserva o que tá na legenda).
- NÃO invente nomes que não estão em B.
- Mantenha capitalização do canônico em B (ex: 'Qifrey', não 'qifrey').
- Se A e B já são idênticos (ex: 'Coco' em ambos), mapeia 'Coco' → 'Coco'.

SAÍDA: APENAS JSON válido, sem markdown. Formato:
{{
  "mapping": {{
    "<nome A>": "<nome B canônico>",
    ...
  }}
}}

Comece direto com `{{`. Sem comentários, sem prefácio.
"""


def build_canonical_mapping(
    detected_names: List[str],
    canonical_chars: List[CharacterInfo],
    api_key: str,
    base_url: str,
    model: str = "gemini-2.5-flash",
    timeout: float = 60.0,
) -> Dict[str, str]:
    """Pede pro LLM mapear nomes detectados → canônicos via similaridade
    fonética. Retorna dict {detected_lower: canonical}.

    Lookup é case-insensitive na chave; valor preserva capitalização.
    """
    if not detected_names or not canonical_chars:
        return {}

    canonical_full = []
    for c in canonical_chars:
        canonical_full.extend(c.all_names())
    # Dedupa preservando ordem
    seen: Set[str] = set()
    canonical_unique = []
    for n in canonical_full:
        if n.lower() not in seen:
            seen.add(n.lower())
            canonical_unique.append(n)

    prompt = _MAPPING_PROMPT.format(
        detected="\n".join(f"- {n}" for n in detected_names),
        canonical="\n".join(f"- {n}" for n in canonical_unique),
    )

    try:
        raw = navy.chat_completion(
            api_key=api_key,
            base_url=base_url,
            model=model,
            messages=[{"role": "user", "content": prompt}],
            timeout=timeout,
            temperature=0.1,  # mapping de nomes quer fidelidade
        )
    except Exception:
        return {}

    # Extrai JSON
    raw = (raw or "").strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        raw = "\n".join(lines).strip()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}

    raw_mapping = data.get("mapping") or {}

    # Filtra: só mantém se valor está na lista canonica E é diferente da
    # chave (mapeamento que faz alguma coisa). Mapping idempotente A→A é
    # ignorado pra economizar substituição.
    canonical_set = {n.lower() for n in canonical_unique}
    out: Dict[str, str] = {}
    for k, v in raw_mapping.items():
        if not k or not v:
            continue
        k = str(k).strip()
        v = str(v).strip()
        if k.lower() == v.lower():
            continue  # nome igual, sem trabalho
        if v.lower() not in canonical_set:
            continue  # LLM alucinou, ignora
        out[k.lower()] = v
    return out


# Palavras iniciais que SINALIZAM nome composto legítimo (não é "primeiro nome").
# Ex: "The Book Selling Witch" deve ficar inteiro; "Mr. Nolnoa" também.
_COMPOUND_NAME_PREFIXES = {
    "the", "a", "an",
    "le", "la", "les", "el", "los", "las", "os", "o",
    "mr", "mr.", "mrs", "mrs.", "ms", "ms.",
    "sr", "sr.", "sra", "sra.",
    "dr", "dr.", "lord", "lady", "sir", "miss",
}


def _short_form(canonical: str) -> str:
    """Devolve forma curta do nome canônico pra usar em substituições.

    - "Agott Arkrome" → "Agott" (primeiro nome — nome próprio)
    - "Qifrey" → "Qifrey" (já é simples)
    - "The Book Selling Witch" → "The Book Selling Witch" (composto, preserva)
    - "Mr. Nolnoa" → "Mr. Nolnoa" (honorífico, preserva)

    Heurística: se o canônico tem 2+ palavras E a 1ª palavra é honorífico/artigo,
    preserva o nome completo. Senão usa só o 1º nome (mais natural na fala).
    """
    parts = canonical.split()
    if len(parts) <= 1:
        return canonical
    first_lower = parts[0].lower().rstrip(",.;:")
    if first_lower in _COMPOUND_NAME_PREFIXES:
        return canonical
    return parts[0]


def apply_mapping_to_text(
    text: str,
    mapping: Dict[str, str],
) -> str:
    """Substitui cada chave do mapping pelo seu valor canônico no texto.

    Case-insensitive. Word-boundary aware (não vai virar "QifreyKieffrey"
    e nem alterar substring no meio de outra palavra).

    Aplica `_short_form()` no valor — "Agott Arkrome" vira "Agott" pra
    soar natural na narração. Nomes compostos legítimos (artigo/honorífico)
    são preservados.
    """
    if not text or not mapping:
        return text

    # Ordena por tamanho desc pra evitar substituir prefixo de outro nome
    # (ex: "Coco" antes de "Cocoa")
    keys = sorted(mapping.keys(), key=lambda s: -len(s))

    for k in keys:
        v = _short_form(mapping[k])
        # Pattern case-insensitive com word boundary
        pattern = re.compile(r"\b" + re.escape(k) + r"\b", flags=re.IGNORECASE)
        text = pattern.sub(v, text)
    return text
