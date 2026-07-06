"""Modal pra escolher a trilha sonora do short.

Modos:
- 🎲 Aleatório: sorteia uma track diferente a cada render (default)
- ✓ Fixo: usa sempre a mesma track escolhida (bom pra branding consistente)
- 🚫 Sem música: render só com narração
"""
import os

import customtkinter as ctk

import config
from core import music
from ui import style
from utils.paths import resource_path


def open_music_picker(parent, current_cfg, on_saved):
    """Abre o modal. `on_saved(new_cfg)` é chamado com config atualizado."""
    modal = ctk.CTkToplevel(parent)
    modal.title("Trilha sonora")
    modal.geometry("520x520")
    modal.resizable(False, False)
    modal.transient(parent)
    modal.grab_set()

    try:
        modal.iconbitmap(resource_path("AniRecap_icon.ico"))
    except Exception:
        pass

    music_dir_path = music.music_dir()
    tracks = music.list_tracks()

    header = ctk.CTkLabel(
        modal,
        text="🎵 Trilha sonora do short",
        font=style.FONT_LABEL,
    )
    header.pack(pady=(16, 4), padx=20, anchor="w")

    hint_text = (
        f"Pasta: {music_dir_path}\n"
        f"Coloque arquivos .mp3/.m4a/.wav/.flac/.ogg lá."
    )
    if not tracks:
        hint_text += "\n\n⚠️ Pasta vazia — adicione músicas."

    hint = ctk.CTkLabel(
        modal, text=hint_text, font=style.FONT_BTN_SECONDARY,
        text_color="#AAA", justify="left", wraplength=480,
    )
    hint.pack(pady=(0, 10), padx=20, anchor="w")

    # Modo de seleção
    current_mode = current_cfg.get("music_mode", "random")
    current_fixed = current_cfg.get("music_fixed_track", "")

    mode_var = ctk.StringVar(value=current_mode)

    rb_random = ctk.CTkRadioButton(
        modal, text="🎲 Aleatório (sorteia diferente a cada render)",
        variable=mode_var, value="random",
        font=style.FONT_BTN_SECONDARY,
    )
    rb_random.pack(anchor="w", padx=20, pady=4)

    rb_none = ctk.CTkRadioButton(
        modal, text="🚫 Sem trilha sonora",
        variable=mode_var, value="none",
        font=style.FONT_BTN_SECONDARY,
    )
    rb_none.pack(anchor="w", padx=20, pady=4)

    # Lista de tracks (fixed mode)
    if tracks:
        rb_fixed_label = ctk.CTkLabel(
            modal, text="✓ Fixo nesta track:",
            font=style.FONT_BTN_SECONDARY,
        )
        rb_fixed_label.pack(anchor="w", padx=20, pady=(8, 4))

        scroll = ctk.CTkScrollableFrame(modal, height=180)
        scroll.pack(padx=20, pady=(0, 10), fill="x")

        # Variável que guarda qual track foi escolhida (filename relativo)
        fixed_var = ctk.StringVar(value=current_fixed)

        def _select_fixed(filename):
            mode_var.set("fixed")
            fixed_var.set(filename)

        for track in tracks:
            filename = os.path.basename(track)
            display = music.display_name(track)
            is_current = (filename == current_fixed and current_mode == "fixed")

            btn = ctk.CTkButton(
                scroll, text=f"🎵 {display}",
                command=lambda f=filename: _select_fixed(f),
                fg_color=(style.BTN_SUCCESS_FG if is_current else style.BTN_DEFAULT_FG),
                hover_color=style.BTN_DEFAULT_HOVER,
                font=style.FONT_BTN_SECONDARY, anchor="w", height=32,
            )
            btn.pack(fill="x", pady=2)

        # Indicador visual de qual está selecionada
        status_lbl = ctk.CTkLabel(
            modal,
            text=f"Selecionada: {music.display_name(current_fixed) if current_fixed else 'nenhuma'}",
            font=("Inter", 10), text_color="gray",
        )
        status_lbl.pack(anchor="w", padx=20)

        def _update_status(*args):
            f = fixed_var.get()
            status_lbl.configure(
                text=f"Selecionada: {music.display_name(f) if f else 'nenhuma'}"
            )

        fixed_var.trace_add("write", _update_status)
    else:
        fixed_var = ctk.StringVar(value="")

    # Botão Salvar
    def _save():
        new_cfg = dict(current_cfg)
        mode = mode_var.get()
        new_cfg["music_mode"] = mode
        if mode == "fixed":
            new_cfg["music_fixed_track"] = fixed_var.get() or current_fixed
        config.save(new_cfg)
        on_saved(new_cfg)
        modal.destroy()

    btn_row = ctk.CTkFrame(modal, fg_color="transparent")
    btn_row.pack(side="bottom", pady=(8, 14), padx=20, fill="x")

    btn_save = ctk.CTkButton(
        btn_row, text="Salvar", command=_save,
        fg_color=style.BTN_SUCCESS_FG, hover_color=style.BTN_DEFAULT_HOVER,
        font=style.FONT_BTN_SECONDARY, width=120, height=32,
    )
    btn_save.pack(side="right")

    btn_cancel = ctk.CTkButton(
        btn_row, text="Cancelar", command=modal.destroy,
        fg_color=style.BTN_DANGER_FG, hover_color=style.BTN_DANGER_HOVER,
        font=style.FONT_BTN_SECONDARY, width=100, height=32,
    )
    btn_cancel.pack(side="right", padx=(0, 8))

    parent.wait_window(modal)
