"""Modal bloqueante para o usuário escolher qual faixa de legenda extrair."""
import customtkinter as ctk

from ui import style
from utils.paths import resource_path


def choose_track(parent, tracks):
    """Mostra um modal com a lista de faixas. Bloqueia até o usuário decidir.

    Devolve o `SubtitleTrack` escolhido ou `None` se cancelado.
    """
    result = {"value": None}

    modal = ctk.CTkToplevel(parent)
    modal.title("Escolher faixa de legenda")
    modal.geometry("540x380")
    modal.resizable(False, False)
    modal.transient(parent)
    modal.grab_set()

    try:
        modal.iconbitmap(resource_path("Ancopy_icon.ico"))
    except Exception:
        pass

    lbl = ctk.CTkLabel(
        modal,
        text=f"Este .mkv tem {len(tracks)} faixas de legenda. Qual usar?",
        font=style.FONT_LABEL,
    )
    lbl.pack(pady=(18, 10), padx=20, anchor="w")

    scroll = ctk.CTkScrollableFrame(modal, width=490, height=240)
    scroll.pack(padx=20, fill="both", expand=True)

    default_idx = next((i for i, t in enumerate(tracks) if t.default), 0)

    def _pick(track):
        result["value"] = track
        modal.destroy()

    for i, track in enumerate(tracks):
        btn = ctk.CTkButton(
            scroll, text=track.label(),
            command=lambda tr=track: _pick(tr),
            fg_color=(style.BTN_SUCCESS_FG if i == default_idx else style.BTN_DEFAULT_FG),
            hover_color=style.BTN_DEFAULT_HOVER,
            font=style.FONT_BTN_SECONDARY, anchor="w", height=36,
        )
        btn.pack(fill="x", pady=3)

    btn_cancel = ctk.CTkButton(
        modal, text="Cancelar", command=modal.destroy,
        fg_color=style.BTN_DANGER_FG, hover_color=style.BTN_DANGER_HOVER,
        font=style.FONT_BTN_SECONDARY, width=120, height=32,
    )
    btn_cancel.pack(pady=(8, 14))

    parent.wait_window(modal)
    return result["value"]
