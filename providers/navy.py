"""Cliente HTTP para Navy AI (https://api.navy/v1).

Navy expõe rotas compatíveis com OpenAI; aqui implementamos apenas o streaming
de /v1/chat/completions que o fluxo de roteiro precisa hoje.
"""
import json
from typing import Iterator, List, Dict

import requests


class NavyError(Exception):
    pass


def chat_completion_stream(
    api_key: str,
    base_url: str,
    model: str,
    messages: List[Dict],
    timeout: float = 120.0,
) -> Iterator[str]:
    """Itera deltas de texto (strings) vindos de POST /v1/chat/completions com stream=True."""
    if not api_key:
        raise NavyError("API key vazia")

    url = base_url.rstrip('/') + '/chat/completions'
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {"model": model, "messages": messages, "stream": True}

    with requests.post(url, headers=headers, json=payload, stream=True, timeout=timeout) as resp:
        if resp.status_code == 401:
            raise NavyError("401: chave inválida ou expirada")
        if resp.status_code == 403:
            raise NavyError("403: seu plano não libera este modelo")
        if resp.status_code == 429:
            raise NavyError("429: limite de uso atingido (Quota exceeded)")
        if resp.status_code >= 400:
            body = ""
            try:
                body = resp.text[:300]
            except Exception:
                pass
            raise NavyError(f"{resp.status_code}: {body}")

        # Alguns servidores SSE não declaram charset; força UTF-8 para não ler
        # acentos como Latin-1 (caso contrário "é" vira "Ã©" no stream).
        resp.encoding = "utf-8"

        for raw in resp.iter_lines(decode_unicode=True):
            if not raw or raw.startswith(':'):
                continue
            if not raw.startswith('data: '):
                continue
            data = raw[6:].strip()
            if data == '[DONE]':
                return
            try:
                obj = json.loads(data)
            except json.JSONDecodeError:
                continue
            # Chunks de erro SSE: `{"error": {...}}` ou `{"error": "..."}`
            if isinstance(obj, dict) and "error" in obj:
                err = obj["error"]
                msg = err.get("message") if isinstance(err, dict) else str(err)
                raise NavyError(f"stream error: {msg}")
            choices = obj.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta") or {}
            content = delta.get("content")
            if content:
                yield content


def chat_completion(
    api_key: str,
    base_url: str,
    model: str,
    messages: List[Dict],
    timeout: float = 180.0,
) -> str:
    """Versão não-streaming. Retorna a string completa ou levanta NavyError.

    Usado como fallback quando o streaming devolve 0 chunks (Gemini via Navy
    às vezes retorna stream vazio sem reportar erro).
    """
    if not api_key:
        raise NavyError("API key vazia")

    url = base_url.rstrip('/') + '/chat/completions'
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {"model": model, "messages": messages}

    resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
    resp.encoding = "utf-8"

    if resp.status_code == 401:
        raise NavyError("401: chave inválida ou expirada")
    if resp.status_code == 403:
        raise NavyError("403: seu plano não libera este modelo")
    if resp.status_code == 429:
        raise NavyError("429: limite de uso atingido (Quota exceeded)")
    if resp.status_code >= 400:
        raise NavyError(f"{resp.status_code}: {resp.text[:300]}")

    try:
        data = resp.json()
    except ValueError:
        raise NavyError(f"resposta não é JSON: {resp.text[:200]}")

    if isinstance(data, dict) and "error" in data:
        err = data["error"]
        msg = err.get("message") if isinstance(err, dict) else str(err)
        raise NavyError(f"API error: {msg}")

    choices = data.get("choices") or []
    if not choices:
        raise NavyError(f"resposta sem choices: {str(data)[:200]}")

    message = choices[0].get("message") or {}
    content = message.get("content") or ""
    if not content:
        finish = choices[0].get("finish_reason", "unknown")
        raise NavyError(f"resposta vazia (finish_reason={finish})")
    return content
