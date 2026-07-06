"""Modal que exibe títulos sugeridos + descrição gerada pelo LLM.

Cada título tem um botão "Copiar" pra clipboard. A descrição também.
Usuário escolhe o que mais gostou e cola direto na publicação.
"""
from __future__ import annotations

from typing import List

import customtkinter as ctk

from ui import style
from utils.paths import resource_path


def open_metadata_modal(parent, titles: List[str], description: str):
    modal = ctk.CTkToplevel(parent)
    modal.title("Títulos & Descrição")
    modal.geometry("560x620")
    modal.resizable(False, False)

    try:
        modal.iconbitmap(resource_path("AniRecap_icon.ico"))
    except Exception:
        pass

    modal.transient(parent)
    modal.grab_set()

    container = ctk.CTkScrollableFrame(modal, fg_color="transparent")
    container.pack(fill="both", expand=True, padx=20, pady=20)

    # Helper pra copiar pro clipboard (usa o tk root pra funcionar mesmo
    # em sub-threads do customtkinter)
    def _copy_to_clipboard(text: str, btn_ref: ctk.CTkButton, original_text: str):
        try:
            modal.clipboard_clear()
            modal.clipboard_append(text)
            modal.update()  # força flush do clipboard antes da janela fechar
        except Exception:
            return
        # Feedback visual rápido
        btn_ref.configure(text="✓ Copiado")
        modal.after(1200, lambda: btn_ref.configure(text=original_text))

    # =========== TÍTULOS ===========
    lbl_titles = ctk.CTkLabel(
        container, text="📌 Títulos sugeridos",
        font=style.FONT_TITLE, anchor="w",
    )
    lbl_titles.pack(fill="x", pady=(0, 4))

    hint = ctk.CTkLabel(
        container,
        text="Clique em \"Copiar\" pra usar o título escolhido.",
        font=("Inter", 10), text_color="gray", anchor="w",
    )
    hint.pack(fill="x", pady=(0, 8))

    for i, title in enumerate(titles, 1):
        row = ctk.CTkFrame(container, fg_color=style.SURFACE, corner_radius=6)
        row.pack(fill="x", pady=4)

        # Texto do título — wrap aceita títulos longos
        lbl = ctk.CTkLabel(
            row, text=f"{i}. {title}",
            font=style.FONT_LABEL, anchor="w", justify="left",
            wraplength=380,
        )
        lbl.pack(side="left", fill="both", expand=True, padx=(10, 4), pady=8)

        original_label = "Copiar"
        btn = ctk.CTkButton(
            row, text=original_label, width=85, height=28,
            font=style.FONT_SMALL,
            fg_color=style.BTN_DEFAULT_FG, hover_color=style.BTN_DEFAULT_HOVER,
        )
        # capture title via default arg pra evitar late binding
        btn.configure(command=lambda t=title, b=btn, ol=original_label:
                      _copy_to_clipboard(t, b, ol))
        btn.pack(side="right", padx=(4, 10), pady=8)

    # =========== DESCRIÇÃO ===========
    lbl_desc = ctk.CTkLabel(
        container, text="📝 Descrição",
        font=style.FONT_TITLE, anchor="w",
    )
    lbl_desc.pack(fill="x", pady=(20, 4))

    desc_box = ctk.CTkTextbox(
        container, height=140, font=style.FONT_LABEL, wrap="word",
        fg_color=style.SURFACE,
    )
    desc_box.pack(fill="x", pady=(0, 8))
    desc_box.insert("1.0", description)
    desc_box.configure(state="disabled")  # somente leitura

    btn_desc_label = "Copiar descrição"
    btn_desc = ctk.CTkButton(
        container, text=btn_desc_label,
        font=style.FONT_BTN_SECONDARY, height=34,
        fg_color=style.BTN_DEFAULT_FG, hover_color=style.BTN_DEFAULT_HOVER,
    )
    btn_desc.configure(command=lambda: _copy_to_clipboard(
        description, btn_desc, btn_desc_label,
    ))
    btn_desc.pack(fill="x")

    # Fechar
    btn_close = ctk.CTkButton(
        container, text="Fechar", command=modal.destroy,
        font=style.FONT_BTN, height=34,
        fg_color=style.BTN_DEFAULT_FG, hover_color=style.BTN_DEFAULT_HOVER,
    )
    btn_close.pack(fill="x", pady=(16, 0))
