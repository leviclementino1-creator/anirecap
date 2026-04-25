"""Extração de legenda interna de arquivos .mkv via MKVToolNix.

Usa `mkvmerge -J` para listar faixas (JSON) e `mkvextract tracks` para extrair,
exatamente como o Inviska MKV Extract faz por baixo.
"""
import json
import os
import subprocess
from dataclasses import dataclass
from typing import List

from utils.binaries import find_binary

# Sem abrir janela de console no Windows quando o app roda como .exe
_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


@dataclass
class SubtitleTrack:
    track_id: int
    codec: str
    language: str
    name: str
    default: bool
    forced: bool

    @property
    def extension(self) -> str:
        codec = self.codec.lower()
        # mkvmerge usa "SubStationAlpha" e "AdvancedSubStationAlpha" — nenhum
        # contém "ass" como substring, então casa pela raiz comum.
        if "stationalpha" in codec or codec in ("ass", "ssa"):
            return ".ass"
        if "subrip" in codec or codec in ("srt", "utf-8", "utf8"):
            return ".srt"
        if "pgs" in codec or "hdmv" in codec:
            return ".sup"
        if "vobsub" in codec:
            return ".sub"
        return ".txt"

    @property
    def is_text(self) -> bool:
        return self.extension in (".ass", ".srt")

    def label(self) -> str:
        parts = [f"#{self.track_id}"]
        if self.language:
            parts.append(self.language.upper())
        if self.name:
            parts.append(self.name)
        parts.append(self.codec)
        tags = []
        if self.default:
            tags.append("default")
        if self.forced:
            tags.append("forced")
        if tags:
            parts.append("[" + ", ".join(tags) + "]")
        return " · ".join(parts)


# Palavras no campo `name` da track que indicam Audio Description.
# Tsundere Raws usa "Descriptive"; outras fontes usam "AD"/"Audio Description"/
# "Descriptive Video"/"DV". Match é case-insensitive e por substring.
_AD_NAME_HINTS = (
    "descriptive",
    "audio description",
    "audio-description",
    "described",
    " ad ",       # espaços pra evitar match em palavras tipo "ada"
    " ad)",
    "(ad ",
    "[ad ",
    " dv ",
    "descritiv",  # PT/ES/FR às vezes
)


@dataclass
class AudioTrack:
    track_id: int
    codec: str
    language: str
    name: str
    default: bool
    channels: int

    @property
    def is_descriptive(self) -> bool:
        """True se o nome/idioma da track indica Audio Description."""
        blob = f" {(self.name or '').lower()} "
        return any(hint in blob for hint in _AD_NAME_HINTS)

    @property
    def extension(self) -> str:
        """Extensão de arquivo que o mkvextract vai produzir pra esse codec."""
        codec = self.codec.lower()
        if "aac" in codec:
            return ".aac"
        if "ac-3" in codec or "ac3" in codec:
            return ".ac3"
        if "e-ac-3" in codec or "eac3" in codec:
            return ".eac3"
        if "opus" in codec:
            return ".opus"
        if "flac" in codec:
            return ".flac"
        if "vorbis" in codec:
            return ".ogg"
        if "dts" in codec:
            return ".dts"
        if "truehd" in codec or "true hd" in codec:
            return ".thd"
        if "pcm" in codec or "wav" in codec:
            return ".wav"
        if "mp3" in codec or "mpeg" in codec:
            return ".mp3"
        return ".audio"

    def label(self) -> str:
        parts = [f"#{self.track_id}"]
        if self.language:
            parts.append(self.language.upper())
        if self.name:
            parts.append(self.name)
        parts.append(self.codec)
        if self.channels:
            parts.append(f"{self.channels}ch")
        tags = []
        if self.is_descriptive:
            tags.append("AD")
        if self.default:
            tags.append("default")
        if tags:
            parts.append("[" + ", ".join(tags) + "]")
        return " · ".join(parts)


def list_subtitle_tracks(mkv_path: str, binaries_dir: str = "") -> List[SubtitleTrack]:
    """Executa `mkvmerge -J mkv_path` e devolve só as faixas do tipo 'subtitles'."""
    mkvmerge = find_binary("mkvmerge", binaries_dir)
    result = subprocess.run(
        [mkvmerge, "-J", mkv_path],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        creationflags=_NO_WINDOW,
    )
    if result.returncode not in (0, 1):
        raise RuntimeError(
            f"mkvmerge falhou (code {result.returncode}): {result.stderr[:300]}"
        )
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"mkvmerge devolveu JSON inválido: {e}")

    tracks: List[SubtitleTrack] = []
    for t in data.get("tracks", []):
        if t.get("type") != "subtitles":
            continue
        props = t.get("properties") or {}
        tracks.append(SubtitleTrack(
            track_id=int(t.get("id")),
            codec=str(t.get("codec") or ""),
            language=str(props.get("language") or ""),
            name=str(props.get("track_name") or ""),
            default=bool(props.get("default_track")),
            forced=bool(props.get("forced_track")),
        ))
    return tracks


def list_audio_tracks(mkv_path: str, binaries_dir: str = "") -> List[AudioTrack]:
    """Executa `mkvmerge -J mkv_path` e devolve só as faixas do tipo 'audio'.

    Útil pra detectar faixas de Audio Description (narração descritiva da
    cena), que Tsundere Raws e Netflix disponibilizam como track separada.
    """
    mkvmerge = find_binary("mkvmerge", binaries_dir)
    result = subprocess.run(
        [mkvmerge, "-J", mkv_path],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        creationflags=_NO_WINDOW,
    )
    if result.returncode not in (0, 1):
        raise RuntimeError(
            f"mkvmerge falhou (code {result.returncode}): {result.stderr[:300]}"
        )
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"mkvmerge devolveu JSON inválido: {e}")

    tracks: List[AudioTrack] = []
    for t in data.get("tracks", []):
        if t.get("type") != "audio":
            continue
        props = t.get("properties") or {}
        tracks.append(AudioTrack(
            track_id=int(t.get("id")),
            codec=str(t.get("codec") or ""),
            language=str(props.get("language") or ""),
            name=str(props.get("track_name") or ""),
            default=bool(props.get("default_track")),
            channels=int(props.get("audio_channels") or 0),
        ))
    return tracks


def find_descriptive_audio_track(
    mkv_path: str, binaries_dir: str = "",
) -> "AudioTrack | None":
    """Conveniência: acha a primeira track de áudio que parece ser AD, ou None.

    A heurística está em `AudioTrack.is_descriptive` (checa nome da track).
    """
    for t in list_audio_tracks(mkv_path, binaries_dir=binaries_dir):
        if t.is_descriptive:
            return t
    return None


def extract_track(mkv_path: str, track_id: int, output_path: str, binaries_dir: str = "") -> str:
    """Extrai a faixa `track_id` do .mkv para `output_path` e devolve o caminho."""
    mkvextract = find_binary("mkvextract", binaries_dir)
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    result = subprocess.run(
        [mkvextract, "tracks", mkv_path, f"{track_id}:{output_path}"],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        creationflags=_NO_WINDOW,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"mkvextract falhou (code {result.returncode}): {result.stderr[:300]}"
        )
    if not os.path.isfile(output_path):
        raise RuntimeError(f"Legenda extraída não apareceu em {output_path}")
    return output_path
