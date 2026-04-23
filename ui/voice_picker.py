"""Modal que lista as vozes da conta ElevenLabs e deixa o usuário escolher.

Carrega em thread pra não travar a UI. Cada linha tem um botão ▶ que baixa o
preview em mp3 (com cache em %TEMP%) e toca no player padrão do sistema.
"""
import os
import subprocess
import sys
import tempfile
import threading
import webbrowser

import customtkinter as ctk
import requests

from providers import elevenlabs
from ui import style
from utils.paths import resource_path


_PREVIEW_CACHE = os.path.join(tempfile.gettempdir(), "ancopy", "previews")


def _play_preview_async(voice_id: str, url: str):
    """Baixa (se ainda não está em cache) e abre no player padrão. Fire-and-forget."""
    def _run():
        try:
            os.makedirs(_PREVIEW_CACHE, exist_ok=True)
            path = os.path.join(_PREVIEW_CACHE, f"{voice_id}.mp3")
            if not os.path.isfile(path) or os.path.getsize(path) == 0:
                resp = requests.get(url, timeout=20)
                resp.raise_for_status()
                with open(path, "wb") as f:
                    f.write(resp.content)
            if os.name == "nt":
                os.startfile(path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception:
            # Último recurso: joga no navegador
            try:
                webbrowser.open(url)
            except Exception:
                pass

    threading.Thread(target=_run, daemon=True).start()


def choose_voice(parent, api_key: str, current_voice_id: str = ""):
    """Devolve o voice_id escolhido, ou None se cancelar / falhar.

    Bloqueia até o modal fechar.
    """
    result = {"value": None}

    modal = ctk.CTkToplevel(parent)
    modal.title("Escolher voz (ElevenLabs)")
    modal.geometry("580x520")
    modal.resizable(False, False)
    modal.transient(parent)
    modal.grab_set()

    try:
        modal.iconbitmap(resource_path("Ancopy_icon.ico"))
    except Exception:
        pass

    status = ctk.CTkLabel(
        modal, text="Carregando vozes...", font=style.FONT_LABEL,
    )
    status.pack(pady=(18, 8))

    scroll = ctk.CTkScrollableFrame(modal, width=540, height=380)
    scroll.pack(padx=20, fill="both", expand=True)

    btn_cancel = ctk.CTkButton(
        modal, text="Cancelar", command=modal.destroy,
        fg_color=style.BTN_DANGER_FG, hover_color=style.BTN_DANGER_HOVER,
        font=style.FONT_BTN_SECONDARY, width=120, height=32,
    )
    btn_cancel.pack(pady=(8, 14))

    def _populate(voices, error):
        if error:
            status.configure(text=f"Erro: {error}")
            return
        if not voices:
            status.configure(text="Nenhuma voz encontrada nesta conta.")
            return

        status.configure(
            text=f"{len(voices)} voz(es) disponíveis — clique para escolher, ▶ para ouvir:",
        )

        for v in voices:
            is_current = v.voice_id == current_voice_id
            btn_color = style.BTN_SUCCESS_FG if is_current else style.BTN_DEFAULT_FG

            row = ctk.CTkFrame(scroll, fg_color="transparent", height=42)
            row.pack(fill="x", pady=3)
            row.pack_propagate(False)

            def _pick(voice=v):
                result["value"] = voice.voice_id
                modal.destroy()

            # Preview PRIMEIRO (side=right) pra garantir espaço;
            # depois o nome com expand=True preenche o resto.
            if v.preview_url:
                def _preview(vid=v.voice_id, url=v.preview_url):
                    _play_preview_async(vid, url)
                btn_preview = ctk.CTkButton(
                    row, text="▶", command=_preview, width=44, height=36,
                    fg_color=style.SURFACE, hover_color=style.HOVER_SUBTLE,
                    font=style.FONT_ICON,
                )
                btn_preview.pack(side="right", padx=(6, 0))

            btn_name = ctk.CTkButton(
                row, text=v.label(), command=_pick,
                fg_color=btn_color, hover_color=style.BTN_DEFAULT_HOVER,
                font=style.FONT_BTN_SECONDARY, anchor="w", height=36,
            )
            btn_name.pack(side="left", fill="x", expand=True)

    def _load():
        try:
            voices = elevenlabs.list_voices(api_key)
            parent.after(0, lambda: _populate(voices, None))
        except elevenlabs.ElevenLabsError as e:
            parent.after(0, lambda msg=str(e): _populate([], msg))
        except Exception as e:
            parent.after(0, lambda msg=str(e): _populate([], msg))

    threading.Thread(target=_load, daemon=True).start()

    parent.wait_window(modal)
    return result["value"]
