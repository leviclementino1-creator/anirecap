"""Extrai envelope de intensidade do áudio do .mkv como PROXY DE IMPACTO
VISUAL sem precisar de computer vision.

INTUIÇÃO: anime tem trilha sonora densa em momentos visualmente importantes
(SFX de impacto, music swells, vocal screams, silêncios dramáticos seguidos
de pico). O envelope de áudio captura isso. Sem CV, é o melhor sinal "free"
de "onde estão os frames de alto interesse".

Pipeline:
1. ffmpeg extrai RMS (volume médio) por janela de 1s do áudio inteiro
2. Detecta ONSETS (picos relativos = SFX/golpes)
3. Detecta POST-SILENCE POPS (silêncio seguido de pico = momento dramático)
4. Cacheia em disco — análise é determinística

Cada cue/timestamp ganha 3 features:
- `energy_db`: nível médio em dB normalizado contra média do ep
- `onset_density`: número de onsets em janela ±2s
- `post_silence_pop`: bool — silêncio sustentado antes (>1.5s <-30dB) +
  pico forte agora (>3dB acima da média)

Uso pelo matcher: prefirir cues com `energy_db>0` + `onset_density>=2` pra
beats de hook/clímax. Cues com `post_silence_pop=true` são money shots
naturais — silêncio dramático antes da revelação.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from utils.binaries import find_binary


_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
_CACHE_DIR = os.path.join(tempfile.gettempdir(), "ancopy", "cache", "audio_envelope")
_PTS_RE = re.compile(r"pts_time:([\d.]+)")
_RMS_RE = re.compile(r"lavfi\.astats\.Overall\.RMS_level=(-?[\d.]+)")


@dataclass
class AudioFeatures:
    """Features de intensidade pra um timestamp específico."""
    timestamp: float
    energy_db: float = 0.0          # delta vs média (positivo = mais alto)
    onset_density: int = 0           # picos em ±2s
    post_silence_pop: bool = False   # silêncio antes + pico agora

    def is_high_intensity(self) -> bool:
        """True quando o frame nessa timestamp provavelmente tem alta carga
        visual: música em pico, SFX de impacto, ou silêncio→explosão."""
        return (
            self.energy_db >= 2.0
            or self.onset_density >= 3
            or self.post_silence_pop
        )

    def short_tag(self) -> str:
        """Tag compacto pra injetar no prompt do matcher."""
        parts = []
        if self.energy_db >= 3.0:
            parts.append("LOUD")
        elif self.energy_db <= -3.0:
            parts.append("quiet")
        if self.onset_density >= 3:
            parts.append("HIGH-ACTION")
        if self.post_silence_pop:
            parts.append("DRAMATIC-POP")
        return ",".join(parts) if parts else ""


@dataclass
class AudioEnvelope:
    """Envelope completo do .mkv. Permite query por timestamp."""
    rms_per_second: List[float] = field(default_factory=list)  # 1 valor por segundo
    onsets: List[float] = field(default_factory=list)           # timestamps de picos
    silences: List[Tuple[float, float]] = field(default_factory=list)  # (start, end)
    duration: float = 0.0
    mean_db: float = -30.0

    def features_at(self, t: float) -> AudioFeatures:
        """Retorna features pra um timestamp (lookups O(log n))."""
        if t < 0 or t > self.duration:
            return AudioFeatures(timestamp=t)

        # 1. energy_db: RMS no segundo `t` - mean_db
        idx = min(int(t), len(self.rms_per_second) - 1)
        energy_db = (self.rms_per_second[idx] - self.mean_db) if self.rms_per_second else 0.0

        # 2. onset_density: onsets em [t-2, t+2]
        onset_density = sum(1 for o in self.onsets if abs(o - t) <= 2.0)

        # 3. post_silence_pop: havia silêncio em [t-2.5, t-0.5] E agora energia >+3dB
        had_silence = any(s_end >= t - 2.5 and s_start <= t - 0.5 for s_start, s_end in self.silences)
        post_silence_pop = had_silence and energy_db >= 3.0

        return AudioFeatures(
            timestamp=t,
            energy_db=round(energy_db, 1),
            onset_density=onset_density,
            post_silence_pop=post_silence_pop,
        )


def _cache_key(mkv_path: str) -> str:
    try:
        size = os.path.getsize(mkv_path)
    except OSError:
        size = 0
    h = hashlib.sha256()
    h.update(mkv_path.encode("utf-8"))
    h.update(str(size).encode("utf-8"))
    return h.hexdigest()[:20]


def _cache_path(mkv_path: str) -> str:
    return os.path.join(_CACHE_DIR, _cache_key(mkv_path) + ".json")


def _extract_rms_per_second(
    mkv_path: str, ffmpeg: str
) -> Tuple[List[float], float]:
    """Extrai RMS_level (dB) do áudio em janelas de 1s.

    Usa filter astats com metadata=print, captura `lavfi.astats.Overall.RMS_level`.
    """
    cmd = [
        ffmpeg, "-i", mkv_path,
        "-vn",
        "-af", "astats=metadata=1:reset=1,ametadata=print:key=lavfi.astats.Overall.RMS_level",
        "-f", "null", "-",
    ]
    r = subprocess.run(
        cmd, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
        creationflags=_NO_WINDOW,
    )
    rms_values: List[float] = []
    duration = 0.0
    for line in (r.stderr or "").splitlines():
        m = _RMS_RE.search(line)
        if m:
            try:
                v = float(m.group(1))
                # Filter dropa valores -inf (silêncio absoluto)
                if not math.isinf(v):
                    rms_values.append(v)
                else:
                    rms_values.append(-90.0)
            except ValueError:
                pass
        elif "Duration:" in line:
            md = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", line)
            if md:
                duration = int(md.group(1)) * 3600 + int(md.group(2)) * 60 + float(md.group(3))
    return rms_values, duration


def _detect_onsets(rms: List[float], threshold_db: float = 4.0) -> List[float]:
    """Detecta onsets como picos locais: rms[t] > rms[t-1] + threshold_db.

    Onset = aumento abrupto de >4dB em 1s. Captura SFX de impacto, beats
    musicais, vocais agudos.
    """
    if len(rms) < 2:
        return []
    onsets = []
    for i in range(1, len(rms)):
        if rms[i] - rms[i - 1] >= threshold_db:
            onsets.append(float(i))
    return onsets


def _detect_silences(
    rms: List[float], silence_db: float = -45.0, min_dur: float = 1.5
) -> List[Tuple[float, float]]:
    """Detecta janelas com RMS sustentado abaixo de `silence_db`.

    Útil pra detectar silêncios dramáticos antes de revelações.
    """
    silences = []
    in_silence = False
    s_start = 0
    for i, v in enumerate(rms):
        if v <= silence_db and not in_silence:
            in_silence = True
            s_start = i
        elif v > silence_db and in_silence:
            in_silence = False
            if i - s_start >= min_dur:
                silences.append((float(s_start), float(i)))
    if in_silence and len(rms) - s_start >= min_dur:
        silences.append((float(s_start), float(len(rms))))
    return silences


def build_envelope(
    mkv_path: str,
    binaries_dir: str = "",
    use_cache: bool = True,
) -> AudioEnvelope:
    """Constrói (ou carrega cache do) envelope do mkv. ~30s no 1º run, instant
    após. Falha silenciosa: erro retorna envelope vazio (matcher continua sem
    sinal de áudio). Não bloqueia pipeline.
    """
    if use_cache:
        cache_file = _cache_path(mkv_path)
        if os.path.isfile(cache_file):
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return AudioEnvelope(
                    rms_per_second=list(data.get("rms_per_second") or []),
                    onsets=list(data.get("onsets") or []),
                    silences=[tuple(s) for s in (data.get("silences") or [])],
                    duration=float(data.get("duration") or 0.0),
                    mean_db=float(data.get("mean_db") or -30.0),
                )
            except Exception:
                pass

    try:
        ffmpeg = find_binary("ffmpeg", binaries_dir)
    except Exception:
        return AudioEnvelope()

    try:
        rms, duration = _extract_rms_per_second(mkv_path, ffmpeg)
    except Exception:
        return AudioEnvelope()

    if not rms:
        return AudioEnvelope()

    # mean_db pra normalizar — usa mediana pra não ser influenciada por OP/ED loud
    sorted_rms = sorted(v for v in rms if v > -80.0)
    mean_db = sorted_rms[len(sorted_rms) // 2] if sorted_rms else -30.0

    onsets = _detect_onsets(rms)
    silences = _detect_silences(rms)

    env = AudioEnvelope(
        rms_per_second=rms,
        onsets=onsets,
        silences=silences,
        duration=duration or float(len(rms)),
        mean_db=mean_db,
    )

    if use_cache:
        try:
            os.makedirs(_CACHE_DIR, exist_ok=True)
            with open(_cache_path(mkv_path), "w", encoding="utf-8") as f:
                json.dump({
                    "rms_per_second": rms,
                    "onsets": onsets,
                    "silences": [list(s) for s in silences],
                    "duration": env.duration,
                    "mean_db": mean_db,
                }, f)
        except Exception:
            pass

    return env
