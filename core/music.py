"""Lista e seleciona trilhas sonoras de fundo da pasta `music/`.

A pasta fica ao lado do executável (raiz do projeto). User joga arquivos
de áudio (.mp3 / .m4a / .wav / .flac / .ogg) lá e o app pega na hora de
renderizar o short final.

Modos de seleção (config):
- `random`: sorteia uma track diferente a cada render
- `fixed`: usa sempre a mesma (path em config.music_fixed_track)
- `none`: render sem trilha sonora
"""
from __future__ import annotations

import os
import random
from typing import List, Optional

from utils.paths import application_path


SUPPORTED_EXTENSIONS = (".mp3", ".m4a", ".wav", ".flac", ".ogg", ".aac")


def music_dir() -> str:
    """Pasta onde o user joga as músicas. Cria se não existir."""
    path = os.path.join(application_path(), "music")
    os.makedirs(path, exist_ok=True)
    return path


def list_tracks(directory: Optional[str] = None) -> List[str]:
    """Lista paths absolutos de arquivos de áudio na pasta de música.

    Ordenado alfabeticamente. Ignora arquivos ocultos.
    """
    directory = directory or music_dir()
    if not os.path.isdir(directory):
        return []

    tracks = []
    for entry in sorted(os.listdir(directory)):
        if entry.startswith("."):
            continue
        full = os.path.join(directory, entry)
        if not os.path.isfile(full):
            continue
        if entry.lower().endswith(SUPPORTED_EXTENSIONS):
            tracks.append(full)
    return tracks


def pick_random(directory: Optional[str] = None) -> Optional[str]:
    """Sorteia uma track. Retorna None se a pasta tá vazia."""
    tracks = list_tracks(directory)
    if not tracks:
        return None
    return random.choice(tracks)


def pick_for_render(
    mode: str,
    fixed_track: str = "",
    directory: Optional[str] = None,
) -> Optional[str]:
    """Resolve qual track usar no render baseado no modo configurado.

    - mode='none': retorna None (sem música)
    - mode='fixed': retorna fixed_track se válido, senão None
    - mode='random' (default): sorteia uma track da pasta
    """
    directory = directory or music_dir()

    if mode == "none":
        return None

    if mode == "fixed" and fixed_track:
        # Aceita path absoluto ou só o filename relativo à pasta de música
        if os.path.isabs(fixed_track) and os.path.isfile(fixed_track):
            return fixed_track
        candidate = os.path.join(directory, fixed_track)
        if os.path.isfile(candidate):
            return candidate
        # Fallback: track configurada não existe mais → cai em random
        return pick_random(directory)

    return pick_random(directory)


def display_name(track_path: str) -> str:
    """Nome amigável pra UI (sem extensão)."""
    if not track_path:
        return ""
    base = os.path.basename(track_path)
    name, _ = os.path.splitext(base)
    return name
