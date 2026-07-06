"""Popup de atualização disponível."""
import threading

import customtkinter as ctk

import updater
from ui import style
from utils.paths import resource_path


def show(parent, link_download: str, nova_versao: str, on_error):
    modal = ctk.CTkToplevel(parent)
    modal.title("Atualização Disponível")
    modal.geometry("380x180")
    modal.resizable(False, False)

    try:
        modal.iconbitmap(resource_path("AniRecap_icon.ico"))
    except Exception:
        pass

    modal.transient(parent)
    modal.grab_set()

    lbl_titulo = ctk.CTkLabel(
        modal, text=f"Versão {nova_versao} disponível!", font=style.FONT_TITLE,
    )
    lbl_titulo.pack(pady=(20, 5))

    lbl_desc = ctk.CTkLabel(
        modal,
        text="Recomendamos atualizar para receber as \nmelhorias e correções mais recentes.",
        font=style.FONT_LABEL,
        text_color="gray",
    )
    lbl_desc.pack(pady=(0, 20))

    frame_botoes = ctk.CTkFrame(modal, fg_color="transparent")
    frame_botoes.pack(fill="x", padx=20)

    btn_atualizar = ctk.CTkButton(
        frame_botoes,
        text="Atualizar Agora",
        fg_color=style.BTN_SUCCESS_FG,
        hover_color=style.BTN_SUCCESS_HOVER,
        font=style.FONT_BTN_SECONDARY,
    )

    def _start():
        btn_atualizar.configure(
            text="Baixando...", state="disabled", fg_color=style.BTN_DEFAULT_HOVER,
        )
        parent.update()

        def _progress(pct):
            # Chamado da thread de download — agenda na thread da UI
            try:
                parent.after(
                    0, lambda: btn_atualizar.configure(text=f"Baixando... {pct}%"),
                )
            except Exception:
                pass

        threading.Thread(
            target=updater.baixar_e_reiniciar,
            args=(link_download, on_error),
            kwargs={"on_progress": _progress},
            daemon=True,
        ).start()

    btn_atualizar.configure(command=_start)
    btn_atualizar.pack(side="left", expand=True, padx=5)

    btn_ignorar = ctk.CTkButton(
        frame_botoes,
        text="Mais Tarde",
        fg_color=style.BTN_DEFAULT_FG,
        hover_color=style.BTN_DEFAULT_HOVER,
        font=style.FONT_BTN_SECONDARY,
        command=modal.destroy,
    )
    btn_ignorar.pack(side="right", expand=True, padx=5)
