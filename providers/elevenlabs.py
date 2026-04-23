"""Cliente HTTP para a API do ElevenLabs.

Por enquanto expõe apenas `list_voices()`; síntese de fala entra quando o
fluxo de TTS for ligado.
"""
from dataclasses import dataclass, field
from typing import List

import requests

_BASE_URL = "https://api.elevenlabs.io/v1"


class ElevenLabsError(Exception):
    pass


@dataclass
class Voice:
    voice_id: str
    name: str
    category: str = ""
    labels: dict = field(default_factory=dict)
    preview_url: str = ""

    def label(self) -> str:
        """Texto curto para o picker. ElevenLabs muitas vezes já põe descrição
        dentro do `name` (ex: "Brian - Laid-Back, Casual"); nesse caso não
        repete tags, só acrescenta gender/accent se couber."""
        if " - " in self.name:
            return self.name
        tags = []
        for key in ("gender", "accent", "age"):
            value = self.labels.get(key)
            if value:
                tags.append(str(value))
        return f"{self.name}  ({', '.join(tags)})" if tags else self.name


def list_voices(api_key: str, timeout: float = 15.0) -> List[Voice]:
    """GET /v1/voices — retorna todas as vozes acessíveis pela chave."""
    if not api_key:
        raise ElevenLabsError("API key vazia")

    resp = requests.get(
        f"{_BASE_URL}/voices",
        headers={"xi-api-key": api_key, "Accept": "application/json"},
        timeout=timeout,
    )
    if resp.status_code == 401:
        raise ElevenLabsError("401: chave ElevenLabs inválida ou sem permissão")
    if resp.status_code == 429:
        raise ElevenLabsError("429: limite de requisições atingido")
    if resp.status_code >= 400:
        raise ElevenLabsError(f"{resp.status_code}: {resp.text[:200]}")

    try:
        data = resp.json()
    except ValueError as e:
        raise ElevenLabsError(f"JSON inválido: {e}")

    voices: List[Voice] = []
    for v in data.get("voices", []):
        voices.append(Voice(
            voice_id=str(v.get("voice_id") or ""),
            name=str(v.get("name") or ""),
            category=str(v.get("category") or ""),
            labels=v.get("labels") or {},
            preview_url=str(v.get("preview_url") or ""),
        ))
    return voices
