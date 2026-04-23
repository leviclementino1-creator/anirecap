"""Fase 3b + 3c — corta beats do .mkv e renderiza o short final.

Pipeline:
1. `cut_clips` → pra cada match no plano, ffmpeg extrai um mp4 silencioso
2. `render_short` → concat + 9:16 com blur background + burn subtitles + mux narração
"""
import os
import subprocess
from typing import List, Tuple

from core.matcher import SceneMatch
from utils.binaries import find_binary

_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def cut_clips(
    mkv_path: str,
    plan: List[SceneMatch],
    out_dir: str,
    binaries_dir: str = "",
    on_progress=None,
) -> List[str]:
    """Corta cada beat do plano em um mp4 separado. Devolve a lista ordenada."""
    ffmpeg = find_binary("ffmpeg", binaries_dir)
    os.makedirs(out_dir, exist_ok=True)

    paths: List[str] = []
    for i, m in enumerate(plan):
        out = os.path.join(out_dir, f"clip_{i:03d}.mp4")
        # -ss antes do -i + re-encode com libx264 = corte preciso
        cmd = [
            ffmpeg, "-y",
            "-ss", f"{m.video_start:.3f}",
            "-i", mkv_path,
            "-t", f"{m.beat.duration:.3f}",
            "-an",
            "-c:v", "libx264", "-crf", "20", "-preset", "veryfast",
            "-pix_fmt", "yuv420p",
            out,
        ]
        r = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            creationflags=_NO_WINDOW,
        )
        if r.returncode != 0 or not os.path.isfile(out):
            raise RuntimeError(
                f"Falha cortando clipe {i+1}/{len(plan)} "
                f"(mkv {m.video_start:.2f}s): {r.stderr[:300]}"
            )
        paths.append(out)
        if on_progress:
            on_progress(i + 1, len(plan))

    return paths


def render_short(
    clip_paths: List[str],
    narration_path: str,
    captions_path: str,
    output_path: str,
    resolution: Tuple[int, int] = (1080, 1920),
    fg_scale: float = 1.15,          # 1.0 = largura da tela; 1.15 = 15% maior (corta as bordas do anime)
    binaries_dir: str = "",
) -> str:
    """Monta o short final: concat de clipes + 9:16 com blur bg + burn captions + narração.

    `fg_scale`: largura do vídeo central em múltiplos da largura da tela.
    1.0 = caber 100%, 1.15 = 15% maior (crop lateral leve). Maior = fica mais
    ocupando a tela; menor = mais blur visível.
    """
    ffmpeg = find_binary("ffmpeg", binaries_dir)
    w, h = resolution
    out_dir = os.path.dirname(output_path)
    os.makedirs(out_dir, exist_ok=True)

    # Arquivo de concat — ffmpeg espera caminhos simples entre aspas simples
    concat_list = os.path.join(out_dir, "_concat.txt")
    with open(concat_list, "w", encoding="utf-8") as f:
        for p in clip_paths:
            abspath = os.path.abspath(p).replace("'", r"'\''")
            f.write(f"file '{abspath}'\n")

    captions_rel = os.path.basename(captions_path)

    fg_w = int(round(w * fg_scale))
    # Overlay é centralizado — (W-w)/2 é negativo quando fg > tela, resultando
    # em crop lateral simétrico.
    filter_complex = (
        f"[0:v]split=2[fg_src][bg_src];"
        f"[bg_src]scale={w}:{h}:force_original_aspect_ratio=increase,"
        f"crop={w}:{h},boxblur=luma_radius=20:luma_power=3[bg];"
        f"[fg_src]scale={fg_w}:-2[fg];"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2[vid];"
        f"[vid]subtitles={captions_rel}[out]"
    )

    cmd = [
        ffmpeg, "-y",
        "-f", "concat", "-safe", "0", "-i", concat_list,
        "-i", narration_path,
        "-filter_complex", filter_complex,
        "-map", "[out]",
        "-map", "1:a",
        "-c:v", "libx264", "-crf", "18", "-preset", "medium",
        "-c:a", "aac", "-b:a", "192k",
        "-pix_fmt", "yuv420p",
        "-shortest",
        os.path.abspath(output_path),
    ]

    r = subprocess.run(
        cmd, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
        cwd=out_dir,  # pra subtitles= achar `captions.ass` sem escape
        creationflags=_NO_WINDOW,
    )

    try:
        os.remove(concat_list)
    except OSError:
        pass

    if r.returncode != 0 or not os.path.isfile(output_path):
        raise RuntimeError(
            f"Render final falhou: {r.stderr[-800:]}"
        )
    return output_path
