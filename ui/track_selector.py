"""Modal bloqueante pra escolher as fontes de contexto do episódio.

Duas decisões independentes:
- Legenda (obrigatória): fonte principal — gera transcript, resumo, roteiro,
  narração. Radio: o usuário escolhe exatamente UMA. CC é pré-selecionado
  quando disponível.
- Audio Description (opcional): canal auxiliar que entra só no matcher de
  cenas, enriquecendo a escolha visual ("ela abre a porta devagar" → usado
  pra achar o frame certo quando o beat não cita diálogo). Checkbox.

Devolve tupla `(subtitle_track, ad_track_or_none)` ou `None` se cancelado.
"""
import customtkinter as ctk

from ui import style
from utils.paths import resource_path


def choose_track(parent, subtitle_tracks, ad_tracks=None):
    """Modal unificado com radio de legenda + checkbox de AD.

    Parâmetros:
        subtitle_tracks: lista de SubtitleTrack (obrigatório, pelo menos 1).
        ad_tracks: lista opcional de AudioTrack AD. Se vazia/None, oculta seção.

    Devolve:
        (SubtitleTrack, AudioTrack | None) — escolha do usuário.
        None — se cancelou.
    """
    ad_tracks = ad_tracks or []
    result = {"value": None}

    modal = ctk.CTkToplevel(parent)
    modal.title("Escolher fontes de contexto")
    modal.geometry("600x560")
    modal.resizable(False, False)
    modal.transient(parent)
    modal.grab_set()

    try:
        modal.iconbitmap(resource_path("Ancopy_icon.ico"))
    except Exception:
        pass

    # Cabeçalho explicativo
    header = (
        f"Este .mkv tem {len(subtitle_tracks)} legenda(s)"
        + (f" e {len(ad_tracks)} faixa(s) de Audio Description." if ad_tracks else ".")
    )
    lbl = ctk.CTkLabel(
        modal, text=header, font=style.FONT_LABEL, wraplength=560,
        justify="left",
    )
    lbl.pack(pady=(16, 6), padx=20, anchor="w")

    hint = ctk.CTkLabel(
        modal,
        text=(
            "• Legenda é usada para o resumo, roteiro e narração.\n"
            "• Audio Description (opcional) descreve o que aparece na tela — "
            "entra só no matcher de cenas para escolher frames melhores."
        ),
        font=style.FONT_BTN_SECONDARY, text_color="#AAA",
        wraplength=560, justify="left",
    )
    hint.pack(pady=(0, 10), padx=20, anchor="w")

    scroll = ctk.CTkScrollableFrame(modal, width=550, height=360)
    scroll.pack(padx=20, fill="both", expand=True)

    # --- Pré-seleção da legenda ---------------------------------------
    def _preferred_sub_idx():
        for i, t in enumerate(subtitle_tracks):
            label = (t.name or '').lower()
            if 'cc' in label or 'caption' in label or 'sdh' in label:
                return i
        for i, t in enumerate(subtitle_tracks):
            if t.default:
                return i
        return 0

    default_sub_idx = _preferred_sub_idx()
    selected_sub = ctk.IntVar(value=default_sub_idx)

    sub_hdr = ctk.CTkLabel(
        scroll, text="📝 Legenda (obrigatória)",
        font=style.FONT_LABEL, anchor="w",
    )
    sub_hdr.pack(fill="x", pady=(0, 4), anchor="w")

    for i, track in enumerate(subtitle_tracks):
        rb = ctk.CTkRadioButton(
            scroll, text=track.label(),
            variable=selected_sub, value=i,
            font=style.FONT_BTN_SECONDARY,
        )
        rb.pack(fill="x", pady=3, anchor="w")

    # --- Pré-seleção e checkbox de AD ---------------------------------
    # Normalmente só há 1 AD track, mas se vierem múltiplas, mostramos
    # radio entre elas + um único checkbox "usar AD".
    use_ad = ctk.BooleanVar(value=bool(ad_tracks))
    selected_ad = ctk.IntVar(value=0)

    if ad_tracks:
        sep = ctk.CTkLabel(scroll, text="", height=4)
        sep.pack(pady=6)

        ad_hdr_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        ad_hdr_frame.pack(fill="x", pady=(0, 4), anchor="w")

        ad_chk = ctk.CTkCheckBox(
            ad_hdr_frame,
            text="🎙️ Audio Description (recomendado — descreve a cena)",
            variable=use_ad,
            font=style.FONT_LABEL,
        )
        ad_chk.pack(side="left", anchor="w")

        # Linhas de AD — se só tem 1, vira label simples; se tem 2+, radio.
        if len(ad_tracks) == 1:
            lbl_ad = ctk.CTkLabel(
                scroll, text=f"   ↳ {ad_tracks[0].label()}",
                font=style.FONT_BTN_SECONDARY, text_color="#CCC", anchor="w",
            )
            lbl_ad.pack(fill="x", pady=3, anchor="w", padx=(24, 0))
        else:
            for i, track in enumerate(ad_tracks):
                rb = ctk.CTkRadioButton(
                    scroll, text=track.label(),
                    variable=selected_ad, value=i,
                    font=style.FONT_BTN_SECONDARY,
                )
                rb.pack(fill="x", pady=3, anchor="w", padx=(24, 0))

    # --- Botões de ação -----------------------------------------------
    btn_frame = ctk.CTkFrame(modal, fg_color="transparent")
    btn_frame.pack(pady=(10, 14), padx=20, fill="x")

    def _confirm():
        sub_idx = selected_sub.get()
        if 0 <= sub_idx < len(subtitle_tracks):
            chosen_sub = subtitle_tracks[sub_idx]
        else:
            chosen_sub = subtitle_tracks[0]
        chosen_ad = None
        if ad_tracks and use_ad.get():
            ad_idx = selected_ad.get()
            if 0 <= ad_idx < len(ad_tracks):
                chosen_ad = ad_tracks[ad_idx]
            else:
                chosen_ad = ad_tracks[0]
        result["value"] = (chosen_sub, chosen_ad)
        modal.destroy()

    btn_ok = ctk.CTkButton(
        btn_frame, text="Confirmar", command=_confirm,
        fg_color=style.BTN_SUCCESS_FG, hover_color=style.BTN_DEFAULT_HOVER,
        font=style.FONT_BTN_SECONDARY, width=140, height=34,
    )
    btn_ok.pack(side="right", padx=(6, 0))

    btn_cancel = ctk.CTkButton(
        btn_frame, text="Cancelar", command=modal.destroy,
        fg_color=style.BTN_DANGER_FG, hover_color=style.BTN_DANGER_HOVER,
        font=style.FONT_BTN_SECONDARY, width=120, height=34,
    )
    btn_cancel.pack(side="right")

    parent.wait_window(modal)
    return result["value"]
