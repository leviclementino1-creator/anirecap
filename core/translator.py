"""Tradução de cues pra português via Gemini (Navy AI).

Usado pra converter AD transcrito (frequentemente em EN ou FR) em PT, já
que o resumo/roteiro/matcher operam em português.

Estratégia:
- Tradução em batch: manda a lista numerada de frases num único prompt,
  recebe a lista traduzida na mesma ordem.
- Preserva timestamps: só o campo `text` muda.
- Robusto a respostas malformadas: se o LLM pular linhas ou renumerar,
  usa fallback por posição com warning.
"""
from __future__ import annotations

from typing import List, Optional

from core.cue import Cue
from providers import navy


# Códigos ISO → nome humano pro prompt. Gemini entende ambos, mas nome completo
# reduz ambiguidade (ex: "pt" vs "pt-BR").
_LANG_NAMES = {
    "en": "English",
    "fr": "French",
    "es": "Spanish",
    "it": "Italian",
    "de": "German",
    "ja": "Japanese",
    "ko": "Korean",
    "zh": "Chinese",
    "pt": "Portuguese",
}


TRANSLATE_PROMPT = """\
Traduza as frases abaixo de {source_lang} para português brasileiro natural
e falado. Contexto: são descrições de cenas de anime (narração descritiva
gravada para acessibilidade), NÃO diálogos de personagens.

REGRAS:
- Traduza literalmente, preservando detalhes visuais e ações.
- NÃO resuma, NÃO combine linhas, NÃO adicione nem remova frases.
- Mantenha o MESMO número de linhas e a MESMA ordem de entrada.
- Use linguagem descritiva clara, português do Brasil.
- Cada linha de saída é uma tradução direta da linha correspondente de entrada.
- Preserve nomes próprios (personagens, lugares).

FORMATO DE SAÍDA (obrigatório):
Uma linha por item, prefixada pelo número e `|`, sem markdown, sem comentários:
1|<tradução da linha 1>
2|<tradução da linha 2>
...

FRASES PARA TRADUZIR ({n} linhas):
{payload}
"""


def _lang_name(code: str) -> str:
    code = (code or "").lower().strip()
    return _LANG_NAMES.get(code, code or "unknown")


def _build_payload(texts: List[str]) -> str:
    """Numeração 1-based pra ajudar o LLM a não se perder."""
    return "\n".join(f"{i + 1}|{t}" for i, t in enumerate(texts))


def _parse_response(raw: str, expected: int) -> List[Optional[str]]:
    """Parse da resposta numerada. Retorna lista com len=expected; entradas
    faltantes ou malformadas viram None (caller decide fallback)."""
    out: List[Optional[str]] = [None] * expected
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line or "|" not in line:
            continue
        num_str, _, txt = line.partition("|")
        num_str = num_str.strip().lstrip("#").rstrip(".")
        try:
            idx = int(num_str) - 1
        except ValueError:
            continue
        if 0 <= idx < expected and txt.strip():
            out[idx] = txt.strip()
    return out


def translate_texts(
    texts: List[str],
    source_lang: str,
    api_key: str,
    base_url: str,
    model: str,
    batch_size: int = 80,
) -> List[str]:
    """Traduz uma lista de strings de `source_lang` pra português.

    Batching: Whisper costuma gerar 200-500 cues por episódio. Mandar tudo
    num call só pode estourar o output (mesmo com max_tokens=8192). Quebra
    em blocos de `batch_size` linhas.

    Fallback: se a tradução de alguma linha vier vazia/malformada, devolve
    o texto original pra aquela posição (evita perder cues — pior caso é
    ter algumas cues em FR misturadas no meio).
    """
    if not texts:
        return []

    src_name = _lang_name(source_lang)
    # Já é português? skip.
    if source_lang.lower().startswith("pt"):
        return list(texts)

    translated: List[str] = []
    for start in range(0, len(texts), batch_size):
        batch = texts[start:start + batch_size]
        payload = _build_payload(batch)
        prompt = TRANSLATE_PROMPT.format(
            source_lang=src_name,
            n=len(batch),
            payload=payload,
        )
        raw = navy.chat_completion(
            api_key=api_key,
            base_url=base_url,
            model=model,
            messages=[{"role": "user", "content": prompt}],
            timeout=180.0,
            temperature=0.2,  # tradução quer fidelidade, não criatividade
        )
        parsed = _parse_response(raw, expected=len(batch))
        for original, got in zip(batch, parsed):
            translated.append(got if got else original)
    return translated


def translate_cues(
    cues: List[Cue],
    source_lang: str,
    api_key: str,
    base_url: str,
    model: str,
) -> List[Cue]:
    """Wrapper que preserva start/end e só traduz o .text de cada cue."""
    if not cues:
        return []
    if source_lang.lower().startswith("pt"):
        return list(cues)

    texts = [c.text for c in cues]
    translated = translate_texts(
        texts=texts,
        source_lang=source_lang,
        api_key=api_key,
        base_url=base_url,
        model=model,
    )
    return [
        Cue(start=c.start, end=c.end, text=t)
        for c, t in zip(cues, translated)
    ]
