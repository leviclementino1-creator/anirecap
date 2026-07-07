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
        modal.iconbitmap(resource_path("AniRecap_icon.ico"))
    except Exception:
        pass

    modal.transient(parent)
    modal.grab_set()

    container = ctk.CTkScrollableFrame(modal, fg_color="transparent")
    container.pack(fill="both", expand=True, padx=20, pady=20)

    # Caminho do config em uso — responde "qual cópia do app estou rodando?"
    # (a instalada tem as keys do usuário; a pasta de distribuição vem vazia).
    ctk.CTkLabel(
        container, text=f"Config: {config.CONFIG_FILE}",
        font=("Inter", 9), text_color="#666", wraplength=420, justify="left",
    ).pack(anchor="w", pady=(0, 10))

    def add_field(label_text, value, show=None):
        lbl = ctk.CTkLabel(container, text=label_text, font=style.FONT_LABEL)
        lbl.pack(anchor="w", pady=(8, 2))
        entry = ctk.CTkEntry(container, width=400, show=show or "")
        entry.pack(fill="x")
        if value:
            entry.insert(0, value)
        return entry

    # =================== Provedor de IA (roteiro/matcher) ===================
    provider_section = ctk.CTkLabel(
        container, text="🤖 Provedor de IA (resumo, roteiro, plano)",
        font=style.FONT_LABEL,
    )
    provider_section.pack(anchor="w", pady=(0, 4))

    provider_hint = ctk.CTkLabel(
        container,
        text="Navy AI = pago, mais estável · Gemini (free) = API oficial do Google,\n"
             "de graça — pegue sua key em aistudio.google.com/apikey",
        font=("Inter", 10), text_color="gray", justify="left",
        wraplength=420,
    )
    provider_hint.pack(anchor="w", pady=(0, 6))

    provider_var = ctk.StringVar(
        value="Gemini (free)" if current.get("llm_provider") == "gemini" else "Navy AI"
    )
    provider_seg = ctk.CTkSegmentedButton(
        container, values=["Navy AI", "Gemini (free)"],
        variable=provider_var, font=style.FONT_SMALL,
    )
    provider_seg.pack(anchor="w", pady=(0, 4), fill="x")

    entry_navy = add_field("Navy AI — API Key", current.get("navy_api_key", ""), show="*")
    entry_navy_url = add_field(
        "Navy AI — Base URL",
        current.get("navy_base_url", config.DEFAULT_NAVY_BASE_URL),
    )
    entry_gemini = add_field(
        "Google Gemini — API Key (free)",
        current.get("gemini_api_key", ""),
        show="*",
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

    # Auto-abrir resultados no player (desligado = silencioso)
    switch_autoopen_var = ctk.BooleanVar(
        value=bool(current.get("auto_open_results", False))
    )
    switch_autoopen = ctk.CTkSwitch(
        container,
        text="Abrir narração/vídeo no player ao concluir (faz barulho)",
        variable=switch_autoopen_var,
        font=style.FONT_LABEL,
    )
    switch_autoopen.pack(anchor="w", pady=(4, 4))

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

    # =================== Voice Settings (ElevenLabs) ===================
    voice_section = ctk.CTkLabel(
        container, text="🎙️ Voice Settings (ElevenLabs)",
        font=style.FONT_LABEL,
    )
    voice_section.pack(anchor="w", pady=(16, 4))

    voice_hint = ctk.CTkLabel(
        container,
        text="stability baixo = mais expressivo · similarity alta = fiel à voz · "
             "style > 0 = drama exagerado",
        font=("Inter", 10), text_color="gray", justify="left",
        wraplength=420,
    )
    voice_hint.pack(anchor="w", pady=(0, 8))

    def _add_slider(label_text: str, key: str, default: float):
        """Slider 0-100 (representa percentual). Internamente salva 0.0-1.0."""
        initial_pct = int(round(float(current.get(key, default)) * 100))
        initial_pct = max(0, min(100, initial_pct))

        row = ctk.CTkFrame(container, fg_color="transparent")
        row.pack(fill="x", pady=(4, 0))

        lbl = ctk.CTkLabel(row, text=label_text, font=style.FONT_SMALL, width=180, anchor="w")
        lbl.pack(side="left")

        value_lbl = ctk.CTkLabel(row, text=f"{initial_pct}%", font=style.FONT_SMALL, width=40)
        value_lbl.pack(side="right")

        var = ctk.IntVar(value=initial_pct)

        def _on_change(v):
            value_lbl.configure(text=f"{int(float(v))}%")

        slider = ctk.CTkSlider(
            row, from_=0, to=100, variable=var, command=_on_change,
            number_of_steps=100,
        )
        slider.pack(side="left", fill="x", expand=True, padx=8)
        return var

    var_stability = _add_slider("Stability", "tts_stability", 0.32)
    var_similarity = _add_slider("Similarity Boost", "tts_similarity_boost", 0.30)
    var_style = _add_slider("Style Exaggeration", "tts_style", 0.0)

    switch_speaker_boost_var = ctk.BooleanVar(
        value=bool(current.get("tts_use_speaker_boost", True))
    )
    switch_speaker_boost = ctk.CTkSwitch(
        container,
        text="Speaker Boost (reforça similaridade)",
        variable=switch_speaker_boost_var,
        font=style.FONT_LABEL,
    )
    switch_speaker_boost.pack(anchor="w", pady=(8, 4))

    # =================== Música de fundo ===================
    music_section = ctk.CTkLabel(
        container, text="🎵 Trilha sonora (música de fundo)",
        font=style.FONT_LABEL,
    )
    music_section.pack(anchor="w", pady=(16, 4))

    music_hint = ctk.CTkLabel(
        container,
        text="Volume em dB (escala logarítmica natural pra mixagem):\n"
             "  0dB = sem atenuar · -20dB = ao fundo (default) · -40dB = quase mudo\n"
             "Pra escolher música/modo aleatório use o botão 🎵 no topo.",
        font=("Inter", 10), text_color="gray", justify="left",
        wraplength=420,
    )
    music_hint.pack(anchor="w", pady=(0, 8))

    # Slider de volume em dB: -40 (quase mudo) até 0 (sem atenuar)
    initial_db = int(round(float(current.get("music_volume_db", -20.0))))
    initial_db = max(-40, min(0, initial_db))

    music_row = ctk.CTkFrame(container, fg_color="transparent")
    music_row.pack(fill="x", pady=(4, 0))

    lbl = ctk.CTkLabel(music_row, text="Volume música", font=style.FONT_SMALL, width=180, anchor="w")
    lbl.pack(side="left")

    music_value_lbl = ctk.CTkLabel(music_row, text=f"{initial_db:+d}dB", font=style.FONT_SMALL, width=50)
    music_value_lbl.pack(side="right")

    var_music_db = ctk.IntVar(value=initial_db)

    def _on_music_change(v):
        db_val = int(float(v))
        music_value_lbl.configure(text=f"{db_val:+d}dB")

    music_slider = ctk.CTkSlider(
        music_row, from_=-40, to=0, variable=var_music_db, command=_on_music_change,
        number_of_steps=40,
    )
    music_slider.pack(side="left", fill="x", expand=True, padx=8)

    # =================== Posição das captions ===================
    captions_section = ctk.CTkLabel(
        container, text="📝 Captions (posição vertical)",
        font=style.FONT_LABEL,
    )
    captions_section.pack(anchor="w", pady=(16, 4))

    captions_hint = ctk.CTkLabel(
        container,
        text="0% = topo da tela · 30% = acima do vídeo · 50% = centro · 75% = embaixo",
        font=("Inter", 10), text_color="gray", justify="left",
        wraplength=420,
    )
    captions_hint.pack(anchor="w", pady=(0, 8))

    initial_cap_pct = int(round(float(current.get("captions_vertical_pct", 0.40)) * 100))
    initial_cap_pct = max(10, min(90, initial_cap_pct))

    cap_row = ctk.CTkFrame(container, fg_color="transparent")
    cap_row.pack(fill="x", pady=(4, 0))

    cap_lbl = ctk.CTkLabel(cap_row, text="Posição vertical", font=style.FONT_SMALL, width=180, anchor="w")
    cap_lbl.pack(side="left")

    cap_value_lbl = ctk.CTkLabel(cap_row, text=f"{initial_cap_pct}%", font=style.FONT_SMALL, width=50)
    cap_value_lbl.pack(side="right")

    var_cap_pct = ctk.IntVar(value=initial_cap_pct)

    def _on_cap_change(v):
        cap_value_lbl.configure(text=f"{int(float(v))}%")

    cap_slider = ctk.CTkSlider(
        cap_row, from_=10, to=90, variable=var_cap_pct, command=_on_cap_change,
        number_of_steps=80,
    )
    cap_slider.pack(side="left", fill="x", expand=True, padx=8)

    # =================== Ritmo dos cortes (sub-cenas) ===================
    cuts_section = ctk.CTkLabel(
        container, text="✂️ Ritmo dos cortes",
        font=style.FONT_LABEL,
    )
    cuts_section.pack(anchor="w", pady=(16, 4))

    cuts_hint = ctk.CTkLabel(
        container,
        text="Tempo médio por sub-cena dentro de cada beat.\n"
             "1.0s = ritmo bem rápido (TikTok pesado) · 2.0s = padrão · 4.0s = cenas longas",
        font=("Inter", 10), text_color="gray", justify="left",
        wraplength=420,
    )
    cuts_hint.pack(anchor="w", pady=(0, 8))

    initial_sub = float(current.get("subclip_target_duration", 2.0))
    initial_sub = max(1.0, min(4.0, initial_sub))

    sub_row = ctk.CTkFrame(container, fg_color="transparent")
    sub_row.pack(fill="x", pady=(4, 0))

    sub_lbl = ctk.CTkLabel(sub_row, text="Tempo médio sub-cena", font=style.FONT_SMALL, width=180, anchor="w")
    sub_lbl.pack(side="left")

    sub_value_lbl = ctk.CTkLabel(sub_row, text=f"{initial_sub:.1f}s", font=style.FONT_SMALL, width=50)
    sub_value_lbl.pack(side="right")

    var_sub = ctk.DoubleVar(value=initial_sub)

    def _on_sub_change(v):
        sub_value_lbl.configure(text=f"{float(v):.1f}s")

    sub_slider = ctk.CTkSlider(
        sub_row, from_=1.0, to=4.0, variable=var_sub, command=_on_sub_change,
        number_of_steps=30,
    )
    sub_slider.pack(side="left", fill="x", expand=True, padx=8)

    # =================== Outras configs ===================
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
            "llm_provider": (
                "gemini" if provider_var.get() == "Gemini (free)" else "navy"
            ),
            "navy_api_key": entry_navy.get().strip(),
            "navy_base_url": entry_navy_url.get().strip() or config.DEFAULT_NAVY_BASE_URL,
            "gemini_api_key": entry_gemini.get().strip(),
            "elevenlabs_api_key": entry_el_key.get().strip(),
            "elevenlabs_voice_id": entry_el_voice.get().strip(),
            "elevenlabs_model_id": entry_el_model.get().strip() or config.DEFAULT_ELEVENLABS_MODEL,
            "tts_speed": speed,
            "tts_silence_cut": bool(switch_silence_var.get()),
            # Voice settings — sliders salvam em 0-100, persiste como 0.0-1.0
            "tts_stability": var_stability.get() / 100.0,
            "tts_similarity_boost": var_similarity.get() / 100.0,
            "tts_style": var_style.get() / 100.0,
            "tts_use_speaker_boost": bool(switch_speaker_boost_var.get()),
            # Música — volume em dB (-40 até 0)
            "music_volume_db": float(var_music_db.get()),
            # Captions — posição vertical (10-90% → 0.10-0.90)
            "captions_vertical_pct": var_cap_pct.get() / 100.0,
            # Ritmo dos cortes — tempo médio por sub-cena (segundos)
            "subclip_target_duration": float(var_sub.get()),
            "use_cache": bool(switch_cache_var.get()),
            "auto_open_results": bool(switch_autoopen_var.get()),
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
