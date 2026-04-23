"""Modal de configurações: chaves da Navy e do ElevenLabs."""
import customtkinter as ctk

import config
from core import cache
from ui import style, voice_picker
from utils.paths import resource_path


def _fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def open_settings(parent, current: dict, on_saved):
    modal = ctk.CTkToplevel(parent)
    modal.title("Configurações")
    modal.geometry("480x600")
    modal.resizable(False, False)

    try:
        modal.iconbitmap(resource_path("Ancopy_icon.ico"))
    except Exception:
        pass

    modal.transient(parent)
    modal.grab_set()

    container = ctk.CTkScrollableFrame(modal, fg_color="transparent")
    container.pack(fill="both", expand=True, padx=20, pady=20)

    def add_field(label_text, value, show=None):
        lbl = ctk.CTkLabel(container, text=label_text, font=style.FONT_LABEL)
        lbl.pack(anchor="w", pady=(8, 2))
        entry = ctk.CTkEntry(container, width=400, show=show or "")
        entry.pack(fill="x")
        if value:
            entry.insert(0, value)
        return entry

    entry_navy = add_field("Navy AI — API Key", current.get("navy_api_key", ""), show="*")
    entry_navy_url = add_field(
        "Navy AI — Base URL",
        current.get("navy_base_url", config.DEFAULT_NAVY_BASE_URL),
    )
    entry_el_key = add_field(
        "ElevenLabs — API Key",
        current.get("elevenlabs_api_key", ""),
        show="*",
    )

    # Voice ID com botão de busca — a lista vem via API quando a chave é válida
    lbl_voice = ctk.CTkLabel(container, text="ElevenLabs — Voice ID", font=style.FONT_LABEL)
    lbl_voice.pack(anchor="w", pady=(8, 2))
    row_voice = ctk.CTkFrame(container, fg_color="transparent")
    row_voice.pack(fill="x")
    entry_el_voice = ctk.CTkEntry(row_voice)
    entry_el_voice.pack(side="left", fill="x", expand=True)
    if current.get("elevenlabs_voice_id"):
        entry_el_voice.insert(0, current["elevenlabs_voice_id"])

    def _open_voice_picker():
        key = entry_el_key.get().strip()
        chosen = voice_picker.choose_voice(modal, key, entry_el_voice.get().strip())
        if chosen:
            entry_el_voice.delete(0, "end")
            entry_el_voice.insert(0, chosen)

    btn_voice = ctk.CTkButton(
        row_voice, text="Buscar...", command=_open_voice_picker,
        fg_color=style.BTN_DEFAULT_FG, hover_color=style.BTN_DEFAULT_HOVER,
        font=style.FONT_SMALL, width=90, height=28,
    )
    btn_voice.pack(side="right", padx=(8, 0))

    entry_el_model = add_field(
        "ElevenLabs — Model ID",
        current.get("elevenlabs_model_id", config.DEFAULT_ELEVENLABS_MODEL),
    )

    # Velocidade da voz (atempo pós-TTS)
    entry_speed = add_field(
        "Velocidade da voz (1.0 = normal, 1.2 = mais rápido)",
        str(current.get("tts_speed", 1.0)),
    )

    # Silence cut (switch)
    switch_silence_var = ctk.BooleanVar(value=bool(current.get("tts_silence_cut", True)))
    switch_silence = ctk.CTkSwitch(
        container,
        text="Cortar silêncios longos na narração",
        variable=switch_silence_var,
        font=style.FONT_LABEL,
    )
    switch_silence.pack(anchor="w", pady=(12, 4))

    # Cache / Modo teste
    switch_cache_var = ctk.BooleanVar(value=bool(current.get("use_cache", False)))
    switch_cache = ctk.CTkSwitch(
        container,
        text="Modo teste — cache de LLM + TTS (economiza tokens)",
        variable=switch_cache_var,
        font=style.FONT_LABEL,
    )
    switch_cache.pack(anchor="w", pady=(4, 4))

    row_cache = ctk.CTkFrame(container, fg_color="transparent")
    row_cache.pack(fill="x", pady=(0, 4))

    lbl_cache_size = ctk.CTkLabel(
        row_cache,
        text=f"Cache atual: {_fmt_size(cache.size_bytes())}",
        font=("Inter", 10), text_color="gray",
    )
    lbl_cache_size.pack(side="left")

    def _clear_cache():
        cache.clear_all()
        lbl_cache_size.configure(text=f"Cache atual: {_fmt_size(cache.size_bytes())}")

    btn_clear_cache = ctk.CTkButton(
        row_cache, text="Limpar cache", command=_clear_cache,
        fg_color=style.BTN_DEFAULT_FG, hover_color=style.BTN_DEFAULT_HOVER,
        font=style.FONT_SMALL, width=110, height=26,
    )
    btn_clear_cache.pack(side="right")

    entry_bin = add_field(
        "Pasta dos binários (fallback — opcional)",
        current.get("binaries_dir", ""),
    )

    hint = ctk.CTkLabel(
        container,
        text="Deixe vazio se o MKVToolNix / ffmpeg estiver no PATH do sistema\n"
             "ou empacotado com o app.",
        font=("Inter", 10), text_color="gray", justify="left",
    )
    hint.pack(anchor="w", pady=(2, 0))

    def _save():
        try:
            speed = float(entry_speed.get().strip().replace(",", "."))
        except ValueError:
            speed = 1.0
        speed = max(0.5, min(3.0, speed))  # sanity cap

        new_cfg = dict(current)
        new_cfg.update({
            "navy_api_key": entry_navy.get().strip(),
            "navy_base_url": entry_navy_url.get().strip() or config.DEFAULT_NAVY_BASE_URL,
            "elevenlabs_api_key": entry_el_key.get().strip(),
            "elevenlabs_voice_id": entry_el_voice.get().strip(),
            "elevenlabs_model_id": entry_el_model.get().strip() or config.DEFAULT_ELEVENLABS_MODEL,
            "tts_speed": speed,
            "tts_silence_cut": bool(switch_silence_var.get()),
            "use_cache": bool(switch_cache_var.get()),
            "binaries_dir": entry_bin.get().strip(),
        })
        config.save(new_cfg)
        on_saved(new_cfg)
        modal.destroy()

    btn_save = ctk.CTkButton(
        container, text="Salvar", command=_save,
        fg_color=style.BTN_DEFAULT_FG, hover_color=style.BTN_DEFAULT_HOVER,
        font=style.FONT_BTN, width=120, height=34,
    )
    btn_save.pack(pady=(20, 4))
