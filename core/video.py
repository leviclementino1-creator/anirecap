"""Fase 3b + 3c — corta beats do .mkv e renderiza o short final.

Pipeline:
1. `cut_clips` → pra cada match no plano, ffmpeg extrai um mp4 silencioso
2. `render_short` → concat + 9:16 com blur background + burn subtitles + mux narração
"""
import math
import os
import subprocess
from typing import List, Optional, Tuple

from core.matcher import SceneMatch
from core.scene_detect import pick_subclips
from utils.binaries import find_binary

_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

# fps fixo dos clipes — garante que cada clipe tem `round(dur * fps)` frames
# exatos. Sem isso, o encoder pode encerrar em keyframe diferente do `-t`,
# causando drift de centenas de ms ao longo de 25+ clipes (= captions
# dessincronizadas).
TARGET_FPS = 30


def cut_clips(
    mkv_path: str,
    plan: List[SceneMatch],
    out_dir: str,
    binaries_dir: str = "",
    on_progress=None,
    scene_changes: Optional[List[float]] = None,
    subclip_target_duration: float = 2.0,
) -> List[str]:
    """Corta cada beat do plano em um mp4 separado.

    Pra cada beat, decide entre:
    - SINGLE-CUT: 1 clip contínuo (modo legado, quando subclip_target_duration
      é >= beat.duration ou não há scene_changes adjacentes pra dividir).
    - MULTI-CUT: 2+ sub-clipes da janela de contexto, concatenados via
      filter_complex trim+concat. Cada sub-clipe ~subclip_target_duration
      seg, respeitando scene changes naturais. Cria ritmo TikTok.
    """
    ffmpeg = find_binary("ffmpeg", binaries_dir)
    os.makedirs(out_dir, exist_ok=True)

    scene_changes = scene_changes or []

    def _scene_start_of(t: float) -> float:
        """Retorna o início da cena que contém o timestamp `t`.
        Procura o maior scene change <= t. Se nenhum, retorna 0.0."""
        prev = 0.0
        for s in scene_changes:
            if s > t:
                break
            prev = s
        return prev

    paths: List[str] = []
    last_scene_start: Optional[float] = None  # cena onde o beat anterior TERMINOU
    prev_video_start: Optional[float] = None  # range temporal do beat anterior
    prev_video_end: Optional[float] = None
    # Drift accumulator: round(dur*fps)/fps difere de dur por até 0.5/fps
    # (~16ms a 30fps). Sobre 25-30 clipes, isso pode acumular 200-500ms,
    # dessincronizando captions e cenas em relação à narração. Cada clipe
    # ganha o drift do anterior somado, e arredonda no novo total — assim
    # a soma das durações REAIS converge pra soma das beat_durations.
    drift_acc = 0.0
    for i, m in enumerate(plan):
        out = os.path.join(out_dir, f"clip_{i:03d}.mp4")

        # Sub-clipes começam em m.video_start (posição que o matcher escolheu),
        # NÃO em m.cue.start — quando matcher snappou, video_start é o ponto bom.
        cue_start = m.video_start

        # OVERLAP GUARD: se este beat sobrepõe TEMPORALMENTE com o anterior
        # (matcher escolheu cenas próximas pra duas metades de uma frase
        # quebrada), desloca cue_start pra fim_do_anterior + small gap.
        # Só ativa quando há overlap real (não em saltos temporais — se o
        # beat anterior estava em 1390 e este em 22, são cenas independentes).
        if (
            prev_video_start is not None
            and prev_video_end is not None
            and prev_video_start <= cue_start <= prev_video_end
        ):
            cue_start = prev_video_end + 0.1

        cue_end = max(
            m.video_end,
            m.cue.end if m.cue else (cue_start + m.beat.duration),
        )
        subclips = pick_subclips(
            cue_start=cue_start,
            cue_end=cue_end,
            beat_duration=m.beat.duration,
            scenes=scene_changes,
            target_subclip_duration=subclip_target_duration,
            avoid_scene_start=last_scene_start,
        )

        # Atualiza state pra próximo beat: cena onde o ÚLTIMO sub-clipe está
        if subclips:
            last_sub_start = subclips[-1][0]
            last_scene_start = _scene_start_of(last_sub_start)
            first_start = subclips[0][0]
            last_end = subclips[-1][0] + subclips[-1][1]
            prev_video_start = first_start
            prev_video_end = last_end

        # Calcula nb_frames considerando drift acumulado dos clipes anteriores.
        # target_dur = duração desejada deste beat + drift carregado.
        # nb_frames arredonda; drift novo = quanto o output ficou além/aquém.
        beat_dur_total = sum(d for _, d in subclips) if len(subclips) > 1 else subclips[0][1]
        target_dur = beat_dur_total + drift_acc
        nb_frames = max(1, round(target_dur * TARGET_FPS))
        real_dur = nb_frames / TARGET_FPS
        drift_acc = target_dur - real_dur  # erro residual pro próximo clipe

        if len(subclips) == 1:
            # === SINGLE-CUT (modo simples) ===
            # NÃO adiciona ghost guard aqui: pick_subclips já entrega timestamps
            # seguros (com ghost guard onde aplicável). Adicionar de novo aqui
            # estouraria a próxima scene boundary = flash visível.
            sub_start, sub_dur = subclips[0]
            target_start = max(0.0, sub_start)
            rough_seek = max(0.0, target_start - 5.0)
            fine_seek = target_start - rough_seek
            cmd = [
                ffmpeg, "-y",
                "-ss", f"{rough_seek:.3f}",
                "-i", mkv_path,
                "-ss", f"{fine_seek:.3f}",
                # filter explícito: fps={TARGET_FPS} antes do encoder garante
                # entrada CFR mesmo se mkv original é VFR (variable framerate).
                # setpts=N/FR/TB reseta PTS pra começar em 0 e ser linear.
                "-vf", f"fps={TARGET_FPS},setpts=N/FR/TB",
                "-frames:v", str(nb_frames),
                "-r", str(TARGET_FPS),
                "-fps_mode", "cfr",
                "-an",
                "-c:v", "libx264", "-crf", "20", "-preset", "veryfast",
                "-pix_fmt", "yuv420p",
                "-video_track_timescale", "30000",
                "-avoid_negative_ts", "make_zero",
                out,
            ]
        else:
            # === MULTI-CUT (filter_complex trim + concat) ===
            # Rough seek pra perto do PRIMEIRO sub-clipe pra acelerar decode.
            # Trim filters usam timestamps RELATIVOS ao -ss.
            rough_seek = max(0.0, min(s for s, _ in subclips) - 1.0)
            trim_filters = []
            concat_inputs = []
            for j, (sub_start, sub_dur) in enumerate(subclips):
                rel_start = sub_start - rough_seek
                rel_end = rel_start + sub_dur
                trim_filters.append(
                    f"[0:v]trim=start={rel_start:.3f}:end={rel_end:.3f},"
                    f"setpts=PTS-STARTPTS[v{j}]"
                )
                concat_inputs.append(f"[v{j}]")
            filter_str = (
                ";".join(trim_filters)
                + ";"
                + "".join(concat_inputs)
                + f"concat=n={len(subclips)}:v=1:a=0,fps={TARGET_FPS}[out]"
            )
            cmd = [
                ffmpeg, "-y",
                "-ss", f"{rough_seek:.3f}",
                "-i", mkv_path,
                "-filter_complex", filter_str,
                "-map", "[out]",
                "-frames:v", str(nb_frames),
                "-fps_mode", "cfr",
                "-an",
                "-c:v", "libx264", "-crf", "20", "-preset", "veryfast",
                "-pix_fmt", "yuv420p",
                "-video_track_timescale", "30000",
                "-avoid_negative_ts", "make_zero",
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
                f"(mkv {m.video_start:.2f}s, {len(subclips)} subs): "
                f"{r.stderr[:300]}"
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
    music_path: Optional[str] = None,
    music_volume_db: float = -20.0,
) -> str:
    """Monta o short final: concat de clipes + 9:16 com blur bg + burn captions + narração.

    `fg_scale`: largura do vídeo central em múltiplos da largura da tela.
    1.0 = caber 100%, 1.15 = 15% maior (crop lateral leve). Maior = fica mais
    ocupando a tela; menor = mais blur visível.

    `music_path`: trilha sonora opcional. Se fornecido, mixa com a narração:
    - narração principal em volume natural
    - música atenuada em `music_volume_db` (default -20dB ≈ 10% do volume linear)
    - escala dB é logarítmica e bate com como o ouvido percebe volume:
        0dB   = sem atenuar (música no nível original)
       -6dB  = metade da percepção
       -12dB = 1/4
       -20dB = bem ao fundo (default)
       -40dB = quase mudo
    - música é loopada com `-stream_loop -1` se for mais curta que o vídeo
    - corte automático no fim do vídeo via `-shortest`
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
    # `fps={TARGET_FPS}` no início força stream contínuo de frames com PTS
    # regulares — sem isso, o concat demuxer pode introduzir micro-gaps que
    # dessincronizam captions e cenas.
    # `setpts=N/FR/TB` reseta o PTS do stream pra começar em 0 e ser linear
    # (1 frame = 1/FPS seg), garantindo que `subtitles=` overlay use o
    # mesmo eixo temporal que a narração.
    video_chain = (
        f"[0:v]fps={TARGET_FPS},setpts=N/FR/TB,split=2[fg_src][bg_src];"
        f"[bg_src]scale={w}:{h}:force_original_aspect_ratio=increase,"
        f"crop={w}:{h},boxblur=luma_radius=20:luma_power=3[bg];"
        f"[fg_src]scale={fg_w}:-2[fg];"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2[vid];"
        f"[vid]subtitles={captions_rel}[out]"
    )

    # Monta comando ffmpeg base.
    # `-fflags +genpts` força ffmpeg a regenerar PTS desde zero em vez de
    # confiar nos timestamps salvos nos .mp4 dos clipes — elimina drift
    # acumulado por inconsistências do concat demuxer.
    cmd = [
        ffmpeg, "-y",
        "-fflags", "+genpts",
        "-f", "concat", "-safe", "0", "-i", concat_list,   # 0: vídeo concat
        "-i", narration_path,                              # 1: narração
    ]

    # Se tem música, adiciona como input 2 com loop infinito
    has_music = music_path and os.path.isfile(music_path)
    if has_music:
        # `-stream_loop -1` repete o áudio infinitamente; `-shortest` corta no fim do vídeo
        cmd += ["-stream_loop", "-1", "-i", os.path.abspath(music_path)]

    # Filter complex: vídeo (sempre) + áudio mixado se há música
    if has_music:
        # Cap em -40dB (mínimo) e 0dB (máximo, sem atenuar)
        db = max(-40.0, min(0.0, float(music_volume_db)))
        # Conversão dB → linear: 10^(dB/20). Ex: -20dB = 0.10
        linear = 10.0 ** (db / 20.0)
        # `amix` por padrão DIVIDE o volume final pelo número de inputs
        # (com 2 inputs → narração cai 50%). Usa `normalize=0` pra desligar
        # essa normalização e mantém a narração no nível original.
        audio_chain = (
            f";[1:a]volume=1.0[narr];"
            f"[2:a]volume={linear:.4f}[music];"
            f"[narr][music]amix=inputs=2:duration=first:"
            f"dropout_transition=0:normalize=0[audio]"
        )
        filter_complex = video_chain + audio_chain
        audio_map = "[audio]"
    else:
        filter_complex = video_chain
        audio_map = "1:a"

    cmd += [
        "-filter_complex", filter_complex,
        "-map", "[out]",
        "-map", audio_map,
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
