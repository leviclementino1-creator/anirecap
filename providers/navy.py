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
    max_tokens: int = 8192,
) -> Iterator[str]:
    """Itera deltas de texto (strings) vindos de POST /v1/chat/completions com stream=True.

    `max_tokens=8192` é alto o suficiente pra resumo longo (~500 palavras PT ≈
    1200 tokens), short_script (~220 palavras ≈ 500 tokens), ou resposta de
    matcher com 30+ beats. Sem esse cap explícito, Navy/Gemini trunca em
    ~400 tokens silenciosamente.
    """
    if not api_key:
        raise NavyError("API key vazia")

    url = base_url.rstrip('/') + '/chat/completions'
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
        "max_tokens": max_tokens,
    }

    last_finish_reason = None
    saw_done = False
    total_chars = 0
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
                saw_done = True
                break
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
            # Guarda finish_reason do último chunk pra detectar truncamento
            fr = choices[0].get("finish_reason")
            if fr:
                last_finish_reason = fr
            delta = choices[0].get("delta") or {}
            content = delta.get("content")
            if content:
                total_chars += len(content)
                yield content

    # Três cenários de problema pra distinguir:
    # 1) finish_reason=length → max_tokens atingido (clássico)
    # 2) stream terminou sem [DONE] e sem finish_reason → provável drop de
    #    conexão mid-stream. Loop iter_lines retorna sem sentinela.
    # 3) finish_reason=content_filter → safety filter do modelo cortou.
    if last_finish_reason == "length":
        raise NavyError(
            f"stream truncado por max_tokens={max_tokens} "
            f"(finish_reason=length). Aumente o limite."
        )
    if last_finish_reason == "content_filter":
        raise NavyError(
            "stream cortado pelo filtro de segurança do modelo "
            "(finish_reason=content_filter). Ajuste o prompt."
        )
    if not saw_done and last_finish_reason is None:
        # Drop silencioso: servidor fechou TCP sem [DONE]. Sinaliza claramente
        # no caller pra decidir se refaz via non-stream.
        raise NavyError(
            f"stream encerrou sem [DONE] nem finish_reason "
            f"(recebidos {total_chars} chars). "
            f"Provável queda de conexão mid-stream — tente de novo."
        )


def chat_completion(
    api_key: str,
    base_url: str,
    model: str,
    messages: List[Dict],
    timeout: float = 180.0,
    temperature: float = None,
    max_tokens: int = 8192,
) -> str:
    """Versão não-streaming. Retorna a string completa ou levanta NavyError.

    Usado como fallback quando o streaming devolve 0 chunks (Gemini via Navy
    às vezes retorna stream vazio sem reportar erro).

    `temperature=0` força determinismo (útil pra tarefas de classificação
    como o matcher). Se None, usa o default do servidor.

    `max_tokens=8192` evita truncamento em resumos/roteiros longos (ver
    chat_completion_stream pra justificativa).
    """
    if not api_key:
        raise NavyError("API key vazia")

    url = base_url.rstrip('/') + '/chat/completions'
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {"model": model, "messages": messages, "max_tokens": max_tokens}
    if temperature is not None:
        payload["temperature"] = temperature

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
