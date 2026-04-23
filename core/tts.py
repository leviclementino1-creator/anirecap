"""Síntese de fala via ElevenLabs com timestamps por caractere.

Usa o endpoint `/v1/text-to-speech/{voice_id}/with-timestamps`, que devolve
o mp3 em base64 + alignment a nível de caractere. O alignment fica salvo em
JSON ao lado do áudio — ele é a fonte pras legendas queimadas do short na
Fase 6 (word-level karaokê).
"""
import base64
import json
import os
from dataclasses import dataclass, field
from typing import List

import requests

_BASE_URL = "https://api.elevenlabs.io/v1"


class TTSError(Exception):
    pass


@dataclass
class Alignment:
    characters: List[str] = field(default_factory=list)
    starts: List[float] = field(default_factory=list)
    ends: List[float] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return self.ends[-1] if self.ends else 0.0

    def to_dict(self) -> dict:
        return {
            "characters": self.characters,
            "character_start_times_seconds": self.starts,
            "character_end_times_seconds": self.ends,
            "duration_seconds": self.duration,
        }


@dataclass
class TTSResult:
    audio_path: str
    alignment_path: str
    alignment: Alignment


def synthesize(
    api_key: str,
    voice_id: str,
    model_id: str,
    text: str,
    output_dir: str,
    base_name: str = "narration",
    timeout: float = 180.0,
) -> TTSResult:
    """Gera narração e grava `narration.mp3` + `narration.alignment.json` em `output_dir`."""
    if not api_key:
        raise TTSError("API key do ElevenLabs vazia")
    if not voice_id:
        raise TTSError("voice_id vazio — escolha uma voz em ⚙️")
    if not text.strip():
        raise TTSError("texto vazio")

    url = f"{_BASE_URL}/text-to-speech/{voice_id}/with-timestamps"
    headers = {
        "xi-api-key": api_key,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    body = {
        "text": text,
        "model_id": model_id or "eleven_multilingual_v2",
    }

    resp = requests.post(url, headers=headers, json=body, timeout=timeout)
    if resp.status_code == 401:
        raise TTSError("401: chave ElevenLabs inválida")
    if resp.status_code == 402:
        raise TTSError("402: créditos insuficientes na conta")
    if resp.status_code == 422:
        raise TTSError(f"422: parâmetros inválidos — {resp.text[:200]}")
    if resp.status_code == 429:
        raise TTSError("429: limite de requisições atingido")
    if resp.status_code >= 400:
        raise TTSError(f"{resp.status_code}: {resp.text[:300]}")

    try:
        data = resp.json()
    except ValueError as e:
        raise TTSError(f"JSON inválido na resposta: {e}")

    audio_b64 = data.get("audio_base64")
    if not audio_b64:
        raise TTSError("Resposta sem audio_base64")

    # `normalized_alignment` é o alignment após normalização do texto (ex: "1" → "um"),
    # que bate 1:1 com os caracteres que foram de fato falados. Preferível para
    # sincronizar caption — o alignment "cru" pode divergir quando há números/siglas.
    alignment_data = data.get("normalized_alignment") or data.get("alignment") or {}
    alignment = Alignment(
        characters=list(alignment_data.get("characters") or []),
        starts=list(alignment_data.get("character_start_times_seconds") or []),
        ends=list(alignment_data.get("character_end_times_seconds") or []),
    )

    os.makedirs(output_dir, exist_ok=True)
    audio_path = os.path.join(output_dir, base_name + ".mp3")
    alignment_path = os.path.join(output_dir, base_name + ".alignment.json")

    with open(audio_path, "wb") as f:
        f.write(base64.b64decode(audio_b64))

    with open(alignment_path, "w", encoding="utf-8") as f:
        json.dump(alignment.to_dict(), f, indent=2, ensure_ascii=False)

    return TTSResult(
        audio_path=audio_path,
        alignment_path=alignment_path,
        alignment=alignment,
    )
