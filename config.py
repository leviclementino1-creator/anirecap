"""Persistência de configuração do app em config.json (ao lado do executável).

Modelo de chaves:
- navy_api_key, navy_base_url                  → provider LLM (Gemini via Navy)
- elevenlabs_api_key, elevenlabs_voice_id, elevenlabs_model_id → TTS (Fase 2+)
- selected_model                               → último modelo usado no botão
"""
import json
import os

from utils.paths import application_path

APP_NAME = "AniRecap"
VERSAO_ATUAL = "2.0.0"

# Repo do GitHub usado pelo auto-update (updater.py consulta
# /releases/latest). Publicar release com tag "vX.Y.Z" + asset AniRecap.zip.
GITHUB_REPO = "leviclementino1-creator/anirecap"

# Auto-update via GitHub Releases. Se o repo ainda não existe ou não tem
# release, o check falha silencioso (sem popup) — seguro deixar ligado.
CHECK_UPDATES = True

DEFAULT_NAVY_BASE_URL = "https://api.navy/v1"
DEFAULT_MODEL = "gemini-2.5-flash"
DEFAULT_FALLBACK_MODEL = "gemini-2.5-flash-lite"
DEFAULT_ELEVENLABS_MODEL = "eleven_multilingual_v2"

CONFIG_FILE = os.path.join(application_path(), 'config.json')

_DEFAULTS = {
    "navy_api_key": "",
    "navy_base_url": DEFAULT_NAVY_BASE_URL,
    "elevenlabs_api_key": "",
    "elevenlabs_voice_id": "",
    "elevenlabs_model_id": DEFAULT_ELEVENLABS_MODEL,
    "selected_model": DEFAULT_MODEL,
    "binaries_dir": "",  # fallback para ffmpeg/mkvmerge/mkvextract
    # Pós-processamento do TTS
    "tts_speed": 1.0,         # fator atempo; 1.0 = normal, 1.2 = mais rápido
    "tts_silence_cut": True,  # cortar pausas longas
    # Voice settings do ElevenLabs (passados no body de /text-to-speech).
    # stability: baixo = mais expressivo/dramático; alto = monotônico
    # similarity_boost: alto = mais fiel à voz original
    # style: exagero estilístico (0.0 = neutro, > 0 = mais drama)
    # use_speaker_boost: reforça similaridade com a voz original
    "tts_stability": 0.32,
    "tts_similarity_boost": 0.30,
    "tts_style": 0.0,
    "tts_use_speaker_boost": True,
    # Trilha sonora de fundo (pasta `music/` ao lado do app)
    "music_mode": "random",        # "random" / "fixed" / "none"
    "music_fixed_track": "",       # filename quando mode="fixed"
    "music_volume_db": -20.0,      # atenuação em dB (-40 = quase mudo, 0 = sem atenuar)
    # Posição vertical das captions (0.0 = topo, 1.0 = base)
    # 0.30 = acima do vídeo central, 0.50 = no meio do vídeo, 0.78 = em baixo
    "captions_vertical_pct": 0.40,
    # Tempo médio por sub-cena dentro de um beat (cortes rápidos estilo TikTok)
    # 1.0 = ritmo bem rápido, 2.0 = padrão, 4.0 = cenas longas
    "subclip_target_duration": 2.0,
    # Cache em disco (economiza tokens em testes repetidos)
    "use_cache": False,
}


def load() -> dict:
    data = dict(_DEFAULTS)
    if os.path.exists(CONFIG_FILE):
        try:
            # utf-8-sig tolera BOM no início do arquivo — alguns editores
            # (e o PowerShell) gravam config.json com BOM, o que faria
            # json.load falhar e o app perder TODAS as configs salvas.
            with open(CONFIG_FILE, 'r', encoding='utf-8-sig') as f:
                saved = json.load(f)
            if isinstance(saved, dict):
                for k, v in saved.items():
                    if k in _DEFAULTS:
                        data[k] = v
        except Exception:
            pass
    return data


def save(cfg: dict) -> None:
    merged = dict(_DEFAULTS)
    for k, v in cfg.items():
        if k in _DEFAULTS:
            merged[k] = v
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(merged, f, indent=2, ensure_ascii=False)
