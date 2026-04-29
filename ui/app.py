"""Janela principal do Ancopy.

Toda a camada de UI vive aqui; parsing, LLM e update são delegados para
`core/`, `providers/` e `updater.py`. A Fase 0 preserva o fluxo do app 1.2.4:
carregar .srt/.ass, limpar transcript, gerar roteiro estilo shorts.
"""
import ctypes
import os
import tempfile
import threading
import time

import customtkinter as ctk
from tkinter import filedialog
from tkinterdnd2 import TkinterDnD, DND_FILES

import config
import updater
import json as _json
import shutil as _shutil

from core import (
    ad_transcribe, anilist, audio_post, cache, captions, chunking, matcher,
    metadata as core_metadata, mkv, music, name_mapper, scene_detect, script,
    subtitle, translator, tts, video,
)
from core.cue import Cue
from providers.navy import NavyError
from ui import (
    metadata_modal, music_picker, settings_modal, style, track_selector,
    update_modal,
)
from utils.binaries import BinaryNotFound
from utils.paths import resource_path

try:
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('ajk.ancopy.app.1.0')
except Exception:
    pass


class SubtitleCleanerApp(ctk.CTk, TkinterDnD.DnDWrapper):
    def __init__(self):
        super().__init__()
        self.TkdndVersion = TkinterDnD._require(self)

        ctk.set_appearance_mode("Dark")
        ctk.set_default_color_theme("blue")

        self.title(f"Ancopy v{config.VERSAO_ATUAL}")
        self.geometry("650x550")
        self.resizable(False, False)

        self.cfg = config.load()
        self.selected_model = self.cfg.get("selected_model") or config.DEFAULT_MODEL

        self.is_loading = False
        self.loading_dots_count = 0

        try:
            self.iconbitmap(resource_path("Ancopy_icon.ico"))
        except Exception:
            pass

        # Estado do pipeline — cada etapa preenche seu campo e libera a próxima.
        self.cues = []
        self.transcript_text = ""
        self.summary_text = ""
        self.short_script_text = ""
        self.last_narration = None  # TTSResult
        self.mkv_path = ""           # caminho do .mkv pra scene detect + cut
        self.subtitle_path = ""      # .ass/.srt extraído (pra detectar OP/ED)
        self.scene_plan = []         # List[SceneMatch] depois de 3a
        # AD é opcional: quando o usuário marca no modal, preenche aqui. Essas
        # cues NÃO entram no transcript/resumo/roteiro — só enriquecem o matcher
        # com descrição visual ("ela abre a porta devagar") pra casar beats
        # com frames do .mkv de forma mais precisa.
        self.ad_cues = []            # List[Cue] — descrição visual transcrita

        # Rótulo da animação de loading — muda por etapa
        self.loading_label = "Processando"

        # diretório de trabalho para artefatos intermediários (mp3, alignment, cortes)
        self.work_dir = os.path.join(tempfile.gettempdir(), "ancopy", "work")
        os.makedirs(self.work_dir, exist_ok=True)

        # fila de log: várias mensagens em rajada animavam em paralelo e
        # embaralhavam caracteres no textbox. Serializa as animações.
        self._log_queue = []
        self._log_animating = False

        self._build_layout()

        self.drop_target_register(DND_FILES)
        self.dnd_bind('<<Drop>>', self._handle_drop)

        if config.CHECK_UPDATES:
            self.after(1000, self._check_update_async)
        self.log("Sistema pronto. Arraste um arquivo...")

    # ------------------------------------------------------------------ layout
    def _build_layout(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # topo: seletor de arquivo + engrenagem
        self.top_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.top_frame.grid(row=0, column=0, padx=20, pady=20, sticky="ew")

        self.btn_select = ctk.CTkButton(
            self.top_frame, text="Carregar .SRT / .ASS / .MKV",
            command=self._select_file,
            fg_color=style.BTN_DEFAULT_FG, hover_color=style.BTN_DEFAULT_HOVER,
            font=style.FONT_BTN, width=90, height=32,
        )
        self.btn_select.pack(side="left")

        self.btn_settings = ctk.CTkButton(
            self.top_frame, text="⚙️", width=35,
            command=self._open_settings,
            fg_color="transparent", text_color="white",
            hover_color=style.HOVER_SUBTLE, font=style.FONT_ICON,
        )
        self.btn_settings.pack(side="right")

        self.btn_music = ctk.CTkButton(
            self.top_frame, text="🎵", width=35,
            command=self._open_music_picker,
            fg_color="transparent", text_color="white",
            hover_color=style.HOVER_SUBTLE, font=style.FONT_ICON,
        )
        self.btn_music.pack(side="right", padx=(0, 4))

        # Seletor de modelo — movido pro topo pra abrir espaço no rodapé
        self.btn_model_selector = ctk.CTkButton(
            self.top_frame, text=f"{self.selected_model}", command=self._toggle_model,
            fg_color=style.BTN_DEFAULT_FG, hover_color=style.BTN_DEFAULT_HOVER,
            font=style.FONT_SMALL, width=140, height=32,
        )
        self.btn_model_selector.pack(side="right", padx=(0, 8))

        # log central
        self.log_box = ctk.CTkTextbox(
            self, font=style.FONT_LOG,
            fg_color=style.BG_DARK, text_color=style.LOG_TEXT,
        )
        self.log_box.grid(row=1, column=0, padx=20, pady=(0, 70), sticky="nsew")
        self.log_box.configure(state="disabled")

        self.btn_copy = ctk.CTkButton(
            self, text="📋", command=self._copy_to_clipboard,
            width=35, height=35,
            fg_color=style.SURFACE, text_color="white",
            hover_color=style.HOVER_SUBTLE, font=style.FONT_ICON,
        )
        self.btn_copy.place_forget()

        # rodapé (5 ações): resumo | short | narração | plano | limpar
        self.btn_ai = ctk.CTkButton(
            self, text="✨ Resumo", command=self._generate_summary,
            fg_color=style.BTN_DEFAULT_FG, hover_color=style.BTN_DEFAULT_HOVER,
            font=style.FONT_BTN_SECONDARY, width=95, height=32, state="disabled",
        )
        self.btn_ai.place(relx=0.03, rely=0.96, anchor="sw")

        self.btn_short = ctk.CTkButton(
            self, text="📝 Short", command=self._generate_short,
            fg_color=style.BTN_DEFAULT_FG, hover_color=style.BTN_DEFAULT_HOVER,
            font=style.FONT_BTN_SECONDARY, width=90, height=32, state="disabled",
        )
        self.btn_short.place(relx=0.19, rely=0.96, anchor="sw")

        self.btn_tts = ctk.CTkButton(
            self, text="🎙️ Narração", command=self._generate_tts,
            fg_color=style.BTN_DEFAULT_FG, hover_color=style.BTN_DEFAULT_HOVER,
            font=style.FONT_BTN_SECONDARY, width=115, height=32, state="disabled",
        )
        self.btn_tts.place(relx=0.34, rely=0.96, anchor="sw")

        self.btn_plano = ctk.CTkButton(
            self, text="🎬 Plano", command=self._generate_plan,
            fg_color=style.BTN_DEFAULT_FG, hover_color=style.BTN_DEFAULT_HOVER,
            font=style.FONT_BTN_SECONDARY, width=95, height=32, state="disabled",
        )
        self.btn_plano.place(relx=0.54, rely=0.96, anchor="sw")

        self.btn_meta = ctk.CTkButton(
            self, text="📋 Meta", command=self._generate_metadata,
            fg_color=style.BTN_DEFAULT_FG, hover_color=style.BTN_DEFAULT_HOVER,
            font=style.FONT_BTN_SECONDARY, width=85, height=32, state="disabled",
        )
        self.btn_meta.place(relx=0.71, rely=0.96, anchor="sw")

        self.btn_clear = ctk.CTkButton(
            self, text="Limpar", command=self._clear_data,
            fg_color=style.BTN_DANGER_FG, hover_color=style.BTN_DANGER_HOVER,
            font=style.FONT_BTN_SECONDARY, width=75, height=32, state="disabled",
        )
        self.btn_clear.place(relx=0.97, rely=0.96, anchor="se")

    # --------------------------------------------------------------- settings
    def _open_settings(self):
        settings_modal.open_settings(self, self.cfg, self._on_settings_saved)

    def _on_settings_saved(self, new_cfg):
        self.cfg = new_cfg
        self.log("Configurações salvas.")

    def _open_music_picker(self):
        music_picker.open_music_picker(self, self.cfg, self._on_settings_saved)

    # ----------------------------------------------------------------- update
    def _check_update_async(self):
        def _run():
            updater.check_async(self._on_update_available)
        threading.Thread(target=_run, daemon=True).start()

    def _on_update_available(self, link, versao):
        self.after(0, lambda: update_modal.show(self, link, versao, self._on_update_error))

    def _on_update_error(self, msg):
        self.after(0, self.log, f"[ERRO] Falha ao baixar atualização: {msg}")

    # ---------------------------------------------------------------- modelos
    def _toggle_model(self):
        self.selected_model = (
            config.DEFAULT_FALLBACK_MODEL
            if self.selected_model == config.DEFAULT_MODEL
            else config.DEFAULT_MODEL
        )
        self.cfg["selected_model"] = self.selected_model
        config.save(self.cfg)
        self.btn_model_selector.configure(text=f"{self.selected_model}")
        self.log(f"Modelo alterado para: {self.selected_model}")

    # -------------------------------------------------------- entrada de arquivo
    def _handle_drop(self, event):
        file_path = event.data
        if file_path.startswith('{') and file_path.endswith('}'):
            file_path = file_path[1:-1]
        lower = file_path.lower()
        if lower.endswith(('.srt', '.ass')):
            self._clear_data()
            self.log(f"Arquivo recebido: {os.path.basename(file_path)}")
            threading.Thread(target=self._process_file, args=(file_path,), daemon=True).start()
        elif lower.endswith('.mkv'):
            self._clear_data()
            self.log(f"Arquivo recebido: {os.path.basename(file_path)}")
            threading.Thread(target=self._process_mkv, args=(file_path,), daemon=True).start()
        else:
            self.log("[ERRO] Formato não suportado. Arraste .srt, .ass ou .mkv")

    def _select_file(self):
        file_path = filedialog.askopenfilename(
            filetypes=[
                ("Todos suportados", "*.srt *.ass *.mkv"),
                ("Legendas", "*.srt *.ass"),
                ("Vídeo MKV", "*.mkv"),
            ],
        )
        if file_path:
            self._clear_data()
            self.log(f"Arquivo detectado: {os.path.basename(file_path)}")
            if file_path.lower().endswith('.mkv'):
                threading.Thread(target=self._process_mkv, args=(file_path,), daemon=True).start()
            else:
                threading.Thread(target=self._process_file, args=(file_path,), daemon=True).start()

    def _process_mkv(self, mkv_path):
        """Lista legendas + AD, modal pra escolher, extrai e cai no fluxo.

        Legenda é sempre a fonte primária do resumo/roteiro/narração.
        AD é opcional — se escolhida, é transcrita em background e guardada
        em self.ad_cues pra enriquecer o matcher de cenas.
        """
        self.mkv_path = mkv_path  # guardado para scene detection + cortes
        try:
            self.after(0, self.log, "Inspecionando faixas do .mkv...")
            binaries_dir = self.cfg.get("binaries_dir", "")

            sub_tracks = mkv.list_subtitle_tracks(mkv_path, binaries_dir=binaries_dir)
            text_tracks = [t for t in sub_tracks if t.is_text]

            try:
                all_audio = mkv.list_audio_tracks(mkv_path, binaries_dir=binaries_dir)
            except Exception:
                all_audio = []
            ad_tracks = [t for t in all_audio if t.is_descriptive]

            if not sub_tracks:
                self.after(0, self.log, "[ERRO] Este .mkv não tem faixas de legenda.")
                return
            if not text_tracks:
                self.after(
                    0, self.log,
                    "[ERRO] Este .mkv só tem legendas de imagem (PGS/VobSub). "
                    "Ainda não suportado."
                )
                return

            if ad_tracks:
                self.after(
                    0, self.log,
                    f"🎙️ {len(ad_tracks)} faixa(s) de Audio Description detectada(s) "
                    "— você pode marcar no modal pra usar como dica visual."
                )

            # Se só tem 1 legenda e não tem AD, pula modal
            if len(text_tracks) == 1 and not ad_tracks:
                chosen_sub = text_tracks[0]
                chosen_ad = None
                self.after(0, self.log, f"Faixa única: {chosen_sub.label()}")
            else:
                pick_result = {"value": None}
                done = threading.Event()

                def _show_modal():
                    pick_result["value"] = track_selector.choose_track(
                        self, text_tracks, ad_tracks=ad_tracks,
                    )
                    done.set()

                self.after(0, _show_modal)
                done.wait()
                picked = pick_result["value"]
                if picked is None:
                    self.after(0, self.log, "Seleção cancelada.")
                    return
                chosen_sub, chosen_ad = picked

            # 1) Pipeline normal da legenda (obrigatório)
            self.after(
                0, self.log,
                f"Extraindo legenda #{chosen_sub.track_id} ({chosen_sub.codec})...",
            )
            out_dir = os.path.join(tempfile.gettempdir(), "ancopy")
            os.makedirs(out_dir, exist_ok=True)
            base = os.path.splitext(os.path.basename(mkv_path))[0]
            sub_out = os.path.join(out_dir, base + chosen_sub.extension)
            mkv.extract_track(
                mkv_path, chosen_sub.track_id, sub_out, binaries_dir=binaries_dir,
            )
            self.after(0, self.log, "Legenda extraída com sucesso.")

            # 2) Se marcou AD, precisamos das cues da legenda PRIMEIRO pra
            # filtrar diálogo duplicado do AD. Parse rápido aqui, sem mexer
            # no estado principal — o _process_file abaixo vai parsear de
            # novo e popular self.cues propriamente.
            self.ad_cues = []
            if chosen_ad is not None:
                try:
                    sub_cues_for_filter = subtitle.load_subtitle(sub_out).cues
                    self.ad_cues = self._extract_ad_cues(
                        mkv_path, chosen_ad, binaries_dir,
                        subtitle_cues=sub_cues_for_filter,
                    )
                except Exception as e:
                    self.after(
                        0, self.log,
                        f"[AVISO] AD falhou ({e}). Vai rodar sem enriquecimento visual."
                    )
                    self.ad_cues = []

            # 3) Carrega o transcript da legenda — daqui pra frente é fluxo legado
            self._process_file(sub_out)

        except BinaryNotFound as e:
            self.after(0, self.log, f"[ERRO] {e}")
        except Exception as e:
            self.after(0, self.log, f"[ERRO] Falha ao processar .mkv: {e}")

    def _extract_ad_cues(self, mkv_path, ad_track, binaries_dir, subtitle_cues=None):
        """Extrai AD via Gemini Flash multimodal (backend Navy).

        Gemini recebe o áudio direto e faz TUDO de uma vez:
        - Ignora diálogo dos personagens (separa por voz no prompt)
        - Transcreve só a narração AD
        - Traduz FR→PT no processo
        - Retorna cues com timestamps [mm:ss]

        Não precisa mais de Whisper local, filtro temporal ou tradução
        separada. `subtitle_cues` ignorado (mantido na assinatura por
        compat, caso um dia seja útil pra outro backend).
        """
        out_dir = os.path.join(tempfile.gettempdir(), "ancopy")
        os.makedirs(out_dir, exist_ok=True)
        base = os.path.splitext(os.path.basename(mkv_path))[0]
        audio_path = os.path.join(out_dir, f"{base}.ad{ad_track.extension}")

        self.after(
            0, self.log,
            f"🎙️ Extraindo áudio AD (#{ad_track.track_id}, {ad_track.codec})...",
        )
        mkv.extract_track(
            mkv_path, ad_track.track_id, audio_path, binaries_dir=binaries_dir,
        )

        # --- Cache por (backend, model, audio fingerprint) ------------
        ad_model = self.cfg.get("ad_model") or "gemini-2.5-flash"
        fingerprint = ad_transcribe.audio_fingerprint(audio_path)
        cache_parts = ["ad-navy", ad_model, fingerprint]

        cached = cache.get_whisper(cache_parts)
        if cached and cached.get("cues"):
            cues = [Cue(start=c[0], end=c[1], text=c[2]) for c in cached["cues"]]
            self.after(
                0, self.log,
                f"🎙️ AD em cache ({len(cues)} segmentos, modelo: {ad_model})."
            )
        else:
            api_key = self.cfg.get("navy_api_key") or ""
            if not api_key:
                self.after(
                    0, self.log,
                    "[ERRO] Sem API Key Navy — não consigo transcrever AD via LLM."
                )
                return []

            base_url = self.cfg.get("navy_base_url") or config.DEFAULT_NAVY_BASE_URL
            self.after(
                0, self.log,
                f"🎙️ Enviando áudio AD pra {ad_model} via Navy "
                f"(compacta pra ~6MB + upload + transcreve + traduz em uma call)..."
            )

            def _progress(msg):
                self.after(0, self.log, f"   ↳ {msg}")

            try:
                result = ad_transcribe.transcribe_audio_via_navy(
                    audio_path=audio_path,
                    api_key=api_key,
                    base_url=base_url,
                    model=ad_model,
                    binaries_dir=binaries_dir,
                    progress=_progress,
                )
            except Exception as e:
                self.after(0, self.log, f"[ERRO] Transcrição Navy falhou: {e}")
                return []

            cues = result.cues
            cache.set_whisper(cache_parts, {
                "cues": [[c.start, c.end, c.text] for c in cues],
                "language": result.language,
                "duration": result.duration,
            })
            self.after(
                0, self.log,
                f"🎙️ {ad_model}: {len(cues)} cues AD (já em PT, já filtrado)."
            )

        if not cues:
            self.after(0, self.log, "[AVISO] AD veio vazia — seguindo sem.")
            return []

        # Dump de debug: SRT pra inspeção manual
        srt_path = os.path.splitext(audio_path)[0] + ".navy.srt"
        try:
            ad_transcribe.dump_cues_as_srt(cues, srt_path)
        except Exception:
            pass

        self.after(
            0, self.log,
            f"✨ AD pronta como dica visual ({len(cues)} cues) — será usada no matcher."
        )
        return cues

    def _normalize_character_names(self):
        """Substitui nomes localizados (KIEFFREY) por canônicos (Qifrey)
        no transcript, cues e ad_cues. Roda 1x por anime, cacheado em disco.

        Não-fatal: qualquer falha (sem rede, sem API key, anime não achado)
        loga warning e segue com nomes da legenda.
        """
        try:
            mkv_path = self.mkv_path or self.subtitle_path
            if not mkv_path:
                return
            base = os.path.basename(mkv_path)
            anime_title = anilist.extract_title_from_filename(base)
            if not anime_title:
                return

            # Cache lookup do AniList (persiste por anime)
            anilist_cache_parts = ["anilist", anime_title.lower()]
            cached = cache.get_anilist(anilist_cache_parts)
            if cached and cached.get("characters"):
                chars = [
                    anilist.CharacterInfo(
                        full=c["full"], role=c.get("role", ""),
                        alternatives=c.get("alternatives", []),
                    )
                    for c in cached["characters"]
                ]
                self.after(
                    0, self.log,
                    f"📚 [cache] AniList: {len(chars)} personagens canônicos "
                    f"de \"{anime_title}\"."
                )
            else:
                self.after(
                    0, self.log,
                    f"📚 Buscando personagens canônicos de \"{anime_title}\" no AniList..."
                )
                chars = anilist.fetch_anime_characters(anime_title)
                if not chars:
                    self.after(
                        0, self.log,
                        f"[INFO] AniList não achou \"{anime_title}\". "
                        "Mantendo nomes da legenda."
                    )
                    return
                cache.set_anilist(anilist_cache_parts, {
                    "title": anime_title,
                    "characters": [
                        {"full": c.full, "role": c.role,
                         "alternatives": c.alternatives}
                        for c in chars
                    ],
                })
                self.after(
                    0, self.log,
                    f"📚 AniList: {len(chars)} personagens canônicos achados."
                )

            # Detecta nomes próprios na transcript
            detected = name_mapper.detect_proper_nouns(self.transcript_text)
            if not detected:
                return

            # Cache do mapping (depende dos detected + canonical, então usa
            # hash de ambos. Persiste pra evitar re-call do Gemini)
            canon_signature = ",".join(sorted(c.full for c in chars))
            mapping_parts = [
                "namemap", anime_title.lower(),
                ",".join(sorted(detected)),
                canon_signature,
            ]
            cached_map = cache.get_anilist(mapping_parts)  # reusa bucket
            if cached_map:
                mapping = cached_map.get("mapping") or {}
            else:
                api_key = self.cfg.get("navy_api_key") or ""
                if not api_key:
                    self.after(
                        0, self.log,
                        "[INFO] Sem API key Navy — pulando mapping de nomes."
                    )
                    return
                base_url = self.cfg.get("navy_base_url") or config.DEFAULT_NAVY_BASE_URL
                self.after(
                    0, self.log,
                    f"🔄 Mapeando {len(detected)} nomes detectados → canônicos via Gemini..."
                )
                mapping = name_mapper.build_canonical_mapping(
                    detected_names=detected,
                    canonical_chars=chars,
                    api_key=api_key,
                    base_url=base_url,
                    model="gemini-2.5-flash",
                )
                cache.set_anilist(mapping_parts, {"mapping": mapping})

            if not mapping:
                self.after(0, self.log, "📚 Nenhuma normalização necessária — nomes já canônicos.")
                return

            # Aplica nas três fontes: transcript_text, self.cues (text de cada
            # cue), e self.ad_cues. Loga sumário das substituições.
            substitutions = ", ".join(
                f"{k}→{v}" for k, v in list(mapping.items())[:5]
            )
            extra = f" e mais {len(mapping) - 5}" if len(mapping) > 5 else ""
            self.after(
                0, self.log,
                f"🔄 Normalizando nomes ({len(mapping)} subst.): {substitutions}{extra}"
            )

            self.transcript_text = name_mapper.apply_mapping_to_text(
                self.transcript_text, mapping,
            )
            self.cues = [
                Cue(
                    start=c.start, end=c.end,
                    text=name_mapper.apply_mapping_to_text(c.text, mapping),
                )
                for c in self.cues
            ]
            if self.ad_cues:
                self.ad_cues = [
                    Cue(
                        start=c.start, end=c.end,
                        text=name_mapper.apply_mapping_to_text(c.text, mapping),
                    )
                    for c in self.ad_cues
                ]
        except Exception as e:
            self.after(0, self.log, f"[AVISO] Normalização de nomes falhou: {e}")

    def _process_file(self, file_path):
        time.sleep(0.8)
        try:
            self.after(0, self.log, "Limpando transcript base...")
            result = subtitle.load_subtitle(file_path)
            self.cues = result.cues
            self.subtitle_path = file_path
            self.transcript_text = result.plain_text
            self.summary_text = ""
            self.short_script_text = ""
            self.last_narration = None

            # Normaliza nomes de personagens via AniList (KIEFFREY → Qifrey,
            # Agathe → Agott, etc). Não-fatal: se falhar, segue com nomes
            # localizados. Atualiza self.cues, self.transcript_text e ad_cues.
            self._normalize_character_names()

            self.after(0, self.log, "Transcript pronto! Clique em ✨ Resumo.")
            self.after(500, lambda: self.btn_copy.place(
                in_=self.log_box, relx=0.98, rely=0.03, anchor="ne",
            ))
            self.after(500, lambda: self.btn_clear.configure(state="normal"))
            self.after(500, lambda: self.btn_ai.configure(state="normal"))
            self.after(500, lambda: self.btn_short.configure(state="disabled"))
            self.after(500, lambda: self.btn_tts.configure(state="disabled"))
        except Exception as e:
            self.after(0, self.log, f"Erro: {str(e)}")

    # ------------------------------------------------------------------ clear
    def _clear_data(self):
        self.cues = []
        self.transcript_text = ""
        self.summary_text = ""
        self.short_script_text = ""
        self.last_narration = None
        self.mkv_path = ""
        self.scene_plan = []
        self.ad_cues = []

        self.btn_copy.place_forget()
        self.btn_clear.configure(state="disabled")
        self.btn_ai.configure(state="disabled")
        self.btn_short.configure(state="disabled")
        self.btn_tts.configure(state="disabled")
        self.btn_plano.configure(state="disabled")
        self.btn_meta.configure(state="disabled")

        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")
        self.log("Área de trabalho limpa.")

    # ------------------------------------------------------------- LLM: resumo
    def _generate_summary(self):
        if not self.cfg.get("navy_api_key"):
            self.log("[ERRO] Insira sua API Key da Navy AI nas configurações (⚙️)")
            return
        if not self.transcript_text.strip():
            self.log("[ERRO] Carregue uma legenda primeiro.")
            return

        # Ao regerar, zera os artefatos a jusante
        self.summary_text = ""
        self.short_script_text = ""
        self.last_narration = None

        self.btn_ai.configure(state="disabled")
        self.btn_short.configure(state="disabled")
        self.btn_tts.configure(state="disabled")
        self.btn_meta.configure(state="disabled")
        self.btn_clear.configure(state="disabled")
        self.loading_label = "Resumindo episódio"
        self.is_loading = True
        self._update_loading_animation()
        threading.Thread(target=self._call_summary, daemon=True).start()

    def _call_summary(self):
        # Cache usa VERSÃO SEMÂNTICA do prompt, não o prompt literal — assim
        # edits cosméticos não invalidam. Bumpar SUMMARY_PROMPT_VERSION em
        # core/script.py pra forçar regen quando mudar a lógica.
        cache_parts = [
            "summary", script.SUMMARY_PROMPT_VERSION, self.selected_model,
            self.transcript_text,
        ]
        try:
            # Cache lookup
            if self.cfg.get("use_cache"):
                cached = cache.get_llm(cache_parts)
                if cached:
                    self.is_loading = False
                    self.after(0, self._clear_loading_line)
                    self.after(
                        0, self.log,
                        f"🗂️ [cache HIT] Resumo ({script.SUMMARY_PROMPT_VERSION}) "
                        "— instantâneo, sem chamar LLM."
                    )
                    self._write_full_to_log(cached, header="--- RESUMO ---", instant=True)
                    self.summary_text = cached.strip()
                    self.after(0, self.log, "Agora clique em 📝 Short pra gerar o roteiro.")
                    self.after(0, lambda: self.btn_short.configure(state="normal"))
                    return

            full = ""
            stream_err = None
            try:
                chunks = script.generate_summary_stream(
                    api_key=self.cfg["navy_api_key"],
                    base_url=self.cfg.get("navy_base_url") or config.DEFAULT_NAVY_BASE_URL,
                    model=self.selected_model,
                    transcript=self.transcript_text,
                )
                full = self._stream_into_log(chunks, header="--- RESUMO ---")
            except NavyError as e:
                stream_err = str(e)
                self.after(0, self.log, f"[AVISO] Stream do resumo falhou: {e}")

            # Resumo tem mínimo mais alto (prompt pede 300-500 palavras).
            # Menos que 200 palavras ou final sem pontuação = provável truncamento.
            def _resumo_truncado(text: str) -> bool:
                t = text.strip()
                if not t:
                    return True
                if len(t.split()) < 200:
                    return True
                return t[-1] not in ".!?\"')"

            if _resumo_truncado(full) or stream_err:
                reason = ("vazio" if not full.strip()
                          else f"curto ({len(full.split())} palavras)"
                          if not stream_err else "erro de stream")
                self.after(
                    0, self.log,
                    f"[AVISO] Resumo {reason} — refazendo via non-stream...",
                )
                try:
                    retry = script.generate_summary(
                        api_key=self.cfg["navy_api_key"],
                        base_url=self.cfg.get("navy_base_url") or config.DEFAULT_NAVY_BASE_URL,
                        model=self.selected_model,
                        transcript=self.transcript_text,
                    )
                    if retry.strip() and not _resumo_truncado(retry):
                        full = retry
                        self._write_full_to_log(full, header="--- RESUMO (retry) ---")
                    elif retry.strip():
                        full = retry
                        self._write_full_to_log(full, header="--- RESUMO (retry, curto) ---")
                        self.after(
                            0, self.log,
                            "[AVISO] Retry também veio curto. Verifique o transcript."
                        )
                except NavyError as e:
                    self.after(0, self.log, f"[ERRO] Retry falhou: {e}")

            if not full.strip():
                self.after(0, self.log, "[ERRO] LLM devolveu vazio. Tente de novo ou troque o modelo.")
                return

            self.summary_text = full.strip()
            if self.cfg.get("use_cache"):
                cache.set_llm(cache_parts, self.summary_text)
            self.after(0, self.log, f"✨ Resumo concluído via {self.selected_model}.")
            self.after(0, self.log, "Agora clique em 📝 Short pra gerar o roteiro.")
            self.after(0, lambda: self.btn_short.configure(state="normal"))
        except NavyError as e:
            self._handle_llm_error(e)
        except Exception as e:
            self.is_loading = False
            self.after(0, self.log, f"\n[ERRO IA]: {str(e)}")
        finally:
            self.after(0, lambda: self.btn_ai.configure(state="normal"))
            self.after(0, lambda: self.btn_clear.configure(state="normal"))
            self.after(0, lambda: self.btn_model_selector.configure(state="normal"))

    # --------------------------------------------------- LLM: roteiro short
    def _generate_short(self):
        if not self.cfg.get("navy_api_key"):
            self.log("[ERRO] Insira sua API Key da Navy AI em ⚙️")
            return
        if not self.summary_text.strip():
            self.log("[ERRO] Gere o resumo primeiro (✨ Resumo).")
            return

        # Ao regerar, zera narração (a jusante)
        self.short_script_text = ""
        self.last_narration = None

        self.btn_ai.configure(state="disabled")
        self.btn_short.configure(state="disabled")
        self.btn_tts.configure(state="disabled")
        self.btn_meta.configure(state="disabled")
        self.btn_clear.configure(state="disabled")
        self.loading_label = "Condensando em roteiro short"
        self.is_loading = True
        self._update_loading_animation()
        threading.Thread(target=self._call_short, daemon=True).start()

    def _call_short(self):
        # Override manual: se existir override_short_script.txt na pasta do app,
        # usa o conteúdo direto (ignora LLM). Garante resultados determinísticos
        # e evita variações não-desejadas. Remove o arquivo pra voltar ao fluxo LLM.
        from utils.paths import application_path
        override = os.path.join(application_path(), "override_short_script.txt")
        if os.path.isfile(override):
            try:
                with open(override, "r", encoding="utf-8") as f:
                    text = f.read().strip()
                if text:
                    self.is_loading = False
                    self.after(0, self._clear_loading_line)
                    self.after(0, self.log, f"📌 Usando short_script do override ({override})")
                    self._write_full_to_log(text, header="--- ROTEIRO SHORT (OVERRIDE) ---", instant=True)
                    self.short_script_text = text
                    self.after(0, self.log, "📝 Override carregado. Clique em 🎙️ Narração.")
                    self.after(0, lambda: self.btn_tts.configure(state="normal"))
                    self.after(0, lambda: self.btn_meta.configure(state="normal"))
                    self.after(0, lambda: self.btn_ai.configure(state="normal"))
                    self.after(0, lambda: self.btn_short.configure(state="normal"))
                    self.after(0, lambda: self.btn_clear.configure(state="normal"))
                    return
            except Exception as e:
                self.after(0, self.log, f"[AVISO] Falha lendo override: {e}, usando LLM normal.")

        # Idem: usa versão semântica, não o prompt literal.
        cache_parts = [
            "short", script.SHORT_SCRIPT_PROMPT_VERSION, self.selected_model,
            self.summary_text,
        ]
        try:
            if self.cfg.get("use_cache"):
                cached = cache.get_llm(cache_parts)
                if cached:
                    self.is_loading = False
                    self.after(0, self._clear_loading_line)
                    self.after(
                        0, self.log,
                        f"🗂️ [cache HIT] Roteiro ({script.SHORT_SCRIPT_PROMPT_VERSION}) "
                        "— instantâneo."
                    )
                    self._write_full_to_log(cached, header="--- ROTEIRO SHORT ---", instant=True)
                    self.short_script_text = cached.strip()
                    self.after(0, self.log, "Clique em 🎙️ Narração.")
                    self.after(0, lambda: self.btn_tts.configure(state="normal"))
                    self.after(0, lambda: self.btn_meta.configure(state="normal"))
                    return

            full = ""
            stream_err = None
            try:
                chunks = script.generate_short_script_stream(
                    api_key=self.cfg["navy_api_key"],
                    base_url=self.cfg.get("navy_base_url") or config.DEFAULT_NAVY_BASE_URL,
                    model=self.selected_model,
                    summary=self.summary_text,
                )
                full = self._stream_into_log(chunks, header="--- ROTEIRO SHORT ---")
            except NavyError as e:
                # Stream encerrou abrupto (drop de conexão, truncamento).
                # Não é erro fatal — o non-stream abaixo é retry confiável.
                stream_err = str(e)
                self.after(0, self.log, f"[AVISO] Stream falhou: {e}")

            # Heurística de truncamento: output MUITO curto pra um short_script,
            # ou não termina em pontuação forte. Dispara fallback non-stream.
            def _looks_truncated(text: str) -> bool:
                t = text.strip()
                if not t:
                    return True
                words = len(t.split())
                if words < 80:  # prompt pede 160-220 palavras
                    return True
                # Se não termina em pontuação de fim de frase, quase certo que cortou
                return t[-1] not in ".!?\"')"

            if _looks_truncated(full) or stream_err:
                reason = ("stream vazio" if not full.strip()
                          else f"truncado ({len(full.split())} palavras)"
                          if not stream_err else "erro de stream")
                self.after(
                    0, self.log,
                    f"[AVISO] Roteiro {reason} — refazendo via non-stream...",
                )
                try:
                    retry = script.generate_short_script(
                        api_key=self.cfg["navy_api_key"],
                        base_url=self.cfg.get("navy_base_url") or config.DEFAULT_NAVY_BASE_URL,
                        model=self.selected_model,
                        summary=self.summary_text,
                    )
                    if retry.strip() and not _looks_truncated(retry):
                        full = retry
                        self._write_full_to_log(full, header="--- ROTEIRO SHORT (retry) ---")
                    elif retry.strip():
                        # Retry também veio meia boca, mas é o melhor que temos
                        full = retry
                        self._write_full_to_log(full, header="--- ROTEIRO SHORT (retry, curto) ---")
                        self.after(
                            0, self.log,
                            "[AVISO] Retry também veio curto. Clique 📝 Short de novo se quiser outro resultado."
                        )
                except NavyError as e:
                    self.after(0, self.log, f"[ERRO] Retry falhou: {e}")

            if not full.strip():
                self.after(
                    0, self.log,
                    "[ERRO] LLM devolveu vazio. Tente clicar 📝 Short de novo ou troque o modelo."
                )
                # NÃO libera o btn_tts — não tem roteiro
                return

            self.short_script_text = full.strip()
            if self.cfg.get("use_cache"):
                cache.set_llm(cache_parts, self.short_script_text)
            self.after(0, self.log, "📝 Roteiro short pronto. Clique em 🎙️ Narração.")
            self.after(0, lambda: self.btn_tts.configure(state="normal"))
            self.after(0, lambda: self.btn_meta.configure(state="normal"))
        except NavyError as e:
            self._handle_llm_error(e)
        except Exception as e:
            self.is_loading = False
            self.after(0, self.log, f"\n[ERRO IA]: {str(e)}")
        finally:
            self.after(0, lambda: self.btn_ai.configure(state="normal"))
            self.after(0, lambda: self.btn_short.configure(state="normal"))
            self.after(0, lambda: self.btn_clear.configure(state="normal"))

    # ------------------------------------------------ helpers de streaming IA
    def _stream_into_log(self, chunks, header: str) -> str:
        """Consome stream da LLM, escreve no log. Retorna "" se o stream
        não produziu nenhum chunk — aí o chamador tenta non-streaming.
        """
        first = True
        full = ""
        for texto_chunk in chunks:
            if first:
                self.is_loading = False
                self.after(0, lambda h=header: self._prepare_log_section(h))
                first = False
            full += texto_chunk
            for char in texto_chunk:
                self.after(0, self._safe_insert, char)
                time.sleep(0.01)
        if first:
            # Stream vazio — não escreve placeholder, deixa o fallback decidir.
            return ""
        self.after(0, lambda: self._safe_insert("\n---\n"))
        return full

    def _write_full_to_log(self, full: str, header: str, instant: bool = False):
        """Escreve um texto completo no log. instant=True = sem animação (pra cache)."""
        self.is_loading = False
        self.after(0, lambda h=header: self._prepare_log_section(h))
        if instant:
            self.after(0, self._safe_insert, full)
        else:
            for char in full:
                self.after(0, self._safe_insert, char)
                time.sleep(0.01)
        self.after(0, lambda: self._safe_insert("\n---\n"))

    def _handle_llm_error(self, err):
        self.is_loading = False
        msg = str(err)
        if "429" in msg:
            self.after(
                0, self.log,
                f"\n[AVISO]: Limite do {self.selected_model} atingido! "
                f"Tente alternar o modelo no botão abaixo. ⏳",
            )
        else:
            self.after(0, self.log, f"\n[ERRO IA]: {msg}")

    # ------------------------------------------------------------------ log
    def log(self, message):
        self._log_queue.append(f"\n> {message}")
        if not self._log_animating:
            self._log_animating = True
            self._drain_log_queue()

    def _drain_log_queue(self):
        if not self._log_queue:
            self._log_animating = False
            return
        text = self._log_queue.pop(0)
        self._type_animation(text, 0)

    def _type_animation(self, text, index=0):
        if index < len(text):
            self.log_box.configure(state="normal")
            self.log_box.insert("end", text[index])
            self.log_box.configure(state="disabled")
            self.log_box.see("end")
            self.after(15, lambda: self._type_animation(text, index + 1))
        else:
            self._drain_log_queue()

    def _safe_insert(self, text):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", text)
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _update_loading_animation(self):
        if self.is_loading:
            self.loading_dots_count = (self.loading_dots_count + 1) % 4
            dots = "." * self.loading_dots_count
            self.log_box.configure(state="normal")
            line_count = int(self.log_box.index("end-1c").split(".")[0])
            self.log_box.delete(f"{line_count}.0", "end")
            self.log_box.insert("end", f"\n> {self.loading_label}{dots}")
            self.log_box.configure(state="disabled")
            self.log_box.see("end")
            self.after(300, self._update_loading_animation)

    def _clear_loading_line(self):
        """Remove a linha atual de loading — útil quando a etapa termina sem stream."""
        self.log_box.configure(state="normal")
        line_count = int(self.log_box.index("end-1c").split(".")[0])
        self.log_box.delete(f"{line_count}.0", "end")
        self.log_box.configure(state="disabled")

    # --------------------------------------------------------------- TTS
    def _generate_tts(self):
        if not self.cfg.get("elevenlabs_api_key"):
            self.log("[ERRO] Configure a API key do ElevenLabs em ⚙️")
            return
        if not self.cfg.get("elevenlabs_voice_id"):
            self.log("[ERRO] Escolha uma voz do ElevenLabs em ⚙️ (Buscar...)")
            return
        if not self.short_script_text.strip():
            self.log("[ERRO] Gere o roteiro short primeiro (📝 Short).")
            return

        self.btn_tts.configure(state="disabled")
        self.btn_ai.configure(state="disabled")
        self.btn_short.configure(state="disabled")
        self.btn_clear.configure(state="disabled")
        self.loading_label = "Gerando narração no ElevenLabs"
        self.is_loading = True
        self._update_loading_animation()
        threading.Thread(target=self._call_tts, daemon=True).start()

    def _call_tts(self):
        voice_id = self.cfg["elevenlabs_voice_id"]
        model_id = self.cfg.get("elevenlabs_model_id") or config.DEFAULT_ELEVENLABS_MODEL
        speed = float(self.cfg.get("tts_speed") or 1.0)
        silence_cut = bool(self.cfg.get("tts_silence_cut", True))
        cache_parts = [
            "tts", "v6-snappy",  # bump quando params de audio_post mudam
            voice_id, model_id,
            f"{speed:.3f}", str(silence_cut),
            self.short_script_text,
        ]

        try:
            # Cache lookup — se bater, copia pro work_dir e pula API + pós-processo
            if self.cfg.get("use_cache"):
                cached_mp3, cached_align = cache.get_tts(cache_parts)
                if cached_mp3 and cached_align:
                    out_mp3 = os.path.join(self.work_dir, "narration.mp3")
                    out_align = os.path.join(self.work_dir, "narration.alignment.json")
                    _shutil.copy(cached_mp3, out_mp3)
                    _shutil.copy(cached_align, out_align)
                    with open(out_align, "r", encoding="utf-8") as f:
                        ad = _json.load(f)
                    align = tts.Alignment(
                        characters=ad.get("characters", []),
                        starts=ad.get("character_start_times_seconds", []),
                        ends=ad.get("character_end_times_seconds", []),
                    )
                    self.last_narration = tts.TTSResult(
                        audio_path=out_mp3, alignment_path=out_align, alignment=align,
                    )
                    self.is_loading = False
                    self.after(0, self._clear_loading_line)
                    self.after(
                        0, self.log,
                        f"🗂️ [cache] Narração carregada ({align.duration:.1f}s)."
                    )
                    # Libera o botão de plano quando temos .mkv + cues
                    if self.mkv_path and self.cues:
                        self.after(0, lambda: self.btn_plano.configure(state="normal"))
                        self.after(
                            0, self.log,
                            "Agora clique em 🎬 Plano pra montar o plano de cortes."
                        )
                    try:
                        if os.name == "nt":
                            os.startfile(out_mp3)  # type: ignore[attr-defined]
                    except Exception:
                        pass
                    return

            result = tts.synthesize(
                api_key=self.cfg["elevenlabs_api_key"],
                voice_id=voice_id,
                model_id=model_id,
                text=self.short_script_text,
                output_dir=self.work_dir,
                stability=self.cfg.get("tts_stability", 0.32),
                similarity_boost=self.cfg.get("tts_similarity_boost", 0.30),
                style=self.cfg.get("tts_style", 0.0),
                use_speaker_boost=self.cfg.get("tts_use_speaker_boost", True),
            )

            # Pós-processamento: silence cut + speed change + ajuste do alignment
            if silence_cut or abs(speed - 1.0) > 0.001:
                try:
                    new_align, stats = audio_post.postprocess(
                        audio_path=result.audio_path,
                        alignment=result.alignment,
                        speed=speed,
                        silence_cut=silence_cut,
                        binaries_dir=self.cfg.get("binaries_dir", ""),
                    )
                    # Regrava o alignment.json com tempos ajustados
                    with open(result.alignment_path, "w", encoding="utf-8") as f:
                        _json.dump(new_align.to_dict(), f, indent=2, ensure_ascii=False)
                    result = tts.TTSResult(
                        audio_path=result.audio_path,
                        alignment_path=result.alignment_path,
                        alignment=new_align,
                    )
                    self.after(
                        0, self.log,
                        f"✂️ Pós-processo: {stats.silences_removed_count} silêncios "
                        f"(−{stats.silences_removed_seconds:.1f}s), velocidade {stats.speed:.2f}x. "
                        f"{stats.original_duration:.1f}s → {stats.final_duration:.1f}s"
                    )
                except BinaryNotFound as e:
                    self.after(
                        0, self.log,
                        f"[AVISO] ffmpeg não encontrado — pulando pós-processamento. {e}"
                    )
                except Exception as e:
                    self.after(
                        0, self.log,
                        f"[AVISO] Pós-processamento falhou: {e}"
                    )

            # Salva no cache se habilitado — tanto mp3 quanto alignment pós-processados
            if self.cfg.get("use_cache"):
                cache.set_tts(cache_parts, result.audio_path, result.alignment_path)

            self.last_narration = result
            self.is_loading = False
            dur = result.alignment.duration
            self.after(0, self._clear_loading_line)
            self.after(
                0, self.log,
                f"✨ Narração pronta ({dur:.1f}s). Salva em: {result.audio_path}",
            )
            # Libera o botão de plano se tivermos .mkv original e cues
            if self.mkv_path and self.cues:
                self.after(0, lambda: self.btn_plano.configure(state="normal"))
                self.after(0, self.log, "Agora clique em 🎬 Plano pra montar o plano de cortes.")
            try:
                if os.name == "nt":
                    os.startfile(result.audio_path)  # type: ignore[attr-defined]
            except Exception:
                pass

        except tts.TTSError as e:
            self.is_loading = False
            self.after(0, self._clear_loading_line)
            self.after(0, self.log, f"[ERRO TTS]: {e}")
        except Exception as e:
            self.is_loading = False
            self.after(0, self._clear_loading_line)
            self.after(0, self.log, f"[ERRO TTS]: {str(e)}")
        finally:
            self.after(0, lambda: self.btn_tts.configure(state="normal"))
            self.after(0, lambda: self.btn_ai.configure(state="normal"))
            self.after(0, lambda: self.btn_short.configure(state="normal"))
            self.after(0, lambda: self.btn_clear.configure(state="normal"))

    # ------------------------------------------------ Plano de cortes (Fase 3a)
    # -------------------------------------------------------- LLM: títulos+desc
    def _generate_metadata(self):
        if not self.short_script_text.strip():
            self.log("[ERRO] Gere o roteiro primeiro (📝 Short).")
            return
        if not self.cfg.get("navy_api_key"):
            self.log("[ERRO] Configure a Navy API key em ⚙️")
            return

        self.btn_meta.configure(state="disabled")
        self.loading_label = "Gerando títulos e descrição"
        self.is_loading = True
        self._update_loading_animation()
        threading.Thread(target=self._call_metadata, daemon=True).start()

    def _call_metadata(self):
        try:
            self.after(0, self.log, "📋 Pedindo títulos + descrição ao LLM...")
            md = core_metadata.generate_metadata(
                short_script=self.short_script_text,
                api_key=self.cfg["navy_api_key"],
                base_url=self.cfg.get("navy_base_url") or config.DEFAULT_NAVY_BASE_URL,
                model=self.selected_model,
            )
            self.is_loading = False
            self.after(0, self._clear_loading_line)
            self.after(0, self.log, f"📋 {len(md.titles)} títulos prontos.")
            self.after(0, lambda: metadata_modal.open_metadata_modal(
                self, md.titles, md.description,
            ))
        except NavyError as e:
            self.is_loading = False
            self.after(0, self._clear_loading_line)
            self._handle_llm_error(e)
        except Exception as e:
            self.is_loading = False
            self.after(0, self._clear_loading_line)
            self.after(0, self.log, f"[ERRO META]: {str(e)}")
        finally:
            self.after(0, lambda: self.btn_meta.configure(state="normal"))

    def _generate_plan(self):
        if not self.last_narration:
            self.log("[ERRO] Gere a narração primeiro.")
            return
        if not self.cues:
            self.log("[ERRO] Sem cues do transcript — só funciona depois de extrair legenda.")
            return
        if not self.mkv_path or not os.path.isfile(self.mkv_path):
            self.log("[ERRO] Arquivo .mkv original não encontrado. Plano precisa do vídeo.")
            return
        if not self.cfg.get("navy_api_key"):
            self.log("[ERRO] Configure a Navy API key em ⚙️")
            return

        self.btn_plano.configure(state="disabled")
        self.btn_clear.configure(state="disabled")
        self.loading_label = "Montando plano de cortes"
        self.is_loading = True
        self._update_loading_animation()
        threading.Thread(target=self._call_plan, daemon=True).start()

    def _call_plan(self):
        try:
            from utils.paths import application_path as _app_path
            app_root = _app_path()

            # 1. Chunk da narração em beats — modo "1 frase = 1 beat".
            # target=1.5s + soft=5.0s + max=7.0s significa:
            # - Qualquer "." ou "!" depois de 1.5s = quebra (frases curtas
            #   tipo "Caraca!" ou "Quase se queimou." viram beats próprios)
            # - Vírgulas só servem como fallback em frases longas (>=5s)
            # - Frases monstruosas (>7s) quebram forçado
            # Comparado com "ABSURDA DE BOA" (target=3.5): quebra em pontos
            # mais cedo, então frases curtas separadas viram beats separados
            # em vez de ficarem coladas. Cada beat = 1 ideia clara → matcher
            # acerta cena específica de cada uma.
            beats = chunking.chunk_by_time(
                self.last_narration.alignment,
                target_seconds=1.5, soft_threshold=2.5, max_seconds=3.5,
            )
            self.is_loading = False
            self.after(0, self._clear_loading_line)
            align = self.last_narration.alignment
            total_dur = align.ends[-1] if (align and align.ends) else 0
            avg = (total_dur / len(beats)) if beats else 0
            self.after(
                0, self.log,
                f"✂️ Narração quebrada em {len(beats)} beats (~{avg:.1f}s cada).",
            )

            # OVERRIDE DE PLANO: se existir override_plan.json na raiz com N entradas
            # casando com beats atuais, usa ele direto e pula matcher+scene_detect+face.
            # Formato: [{"video_start": float, "why": str opcional, "snapped": bool opcional}, ...]
            override_plan_path = os.path.join(app_root, "override_plan.json")
            if os.path.isfile(override_plan_path):
                try:
                    with open(override_plan_path, "r", encoding="utf-8") as f:
                        override_items = _json.load(f)
                    if not isinstance(override_items, list):
                        raise ValueError("override_plan.json não é uma lista")
                    if len(override_items) != len(beats):
                        self.after(
                            0, self.log,
                            f"[AVISO] override_plan.json tem {len(override_items)} items "
                            f"mas a narração gerou {len(beats)} beats — ignorando override."
                        )
                    else:
                        self.after(0, self.log, f"📌 Usando plano do override ({override_plan_path})")
                        plan = []
                        for b, item in zip(beats, override_items):
                            vs = float(item["video_start"])
                            plan.append(matcher.SceneMatch(
                                beat=b, cue=None,
                                video_start=vs, video_end=vs + b.duration,
                                snapped=bool(item.get("snapped", False)),
                                why=str(item.get("why", "[override]")),
                            ))
                        self.scene_plan = plan
                        # Override path ainda precisa de scenes pra multi-cut.
                        # detect_scenes usa cache em disco, então é rápido em re-runs.
                        self.after(0, self.log, "🎞️ Detectando cenas (pra multi-cut nos clipes)...")
                        scenes_for_override = scene_detect.detect_scenes(
                            self.mkv_path, threshold=0.35,
                            binaries_dir=self.cfg.get("binaries_dir", ""),
                            use_cache=True,
                        )
                        self._finish_plan_and_render(plan, scenes=scenes_for_override)
                        return
                except Exception as e:
                    self.after(0, self.log, f"[AVISO] Falha lendo override_plan.json: {e}")

            # 2. Scene detection (cacheada)
            self.after(0, self.log, "🎞️ Detectando mudanças de cena no .mkv (pode levar ~30s na 1ª vez)...")
            scenes = scene_detect.detect_scenes(
                self.mkv_path, threshold=0.35,
                binaries_dir=self.cfg.get("binaries_dir", ""),
                use_cache=True,
            )
            self.after(0, self.log, f"🎞️ {len(scenes)} mudanças de cena detectadas.")

            # 2b. Detecta regiões de OP/ED. Duas estratégias combinadas:
            # (1) Estilo "OP/ED/Song" no .ass — funciona se o ripper tagueou.
            # (2) Gaps longos no diálogo (>45s) — funciona com SubsPlease etc
            #     que deixam OP sem subtítulo algum.
            from core.audio_post import _ffprobe_duration
            mkv_dur = _ffprobe_duration(self.mkv_path, self.cfg.get("binaries_dir", ""))

            # Parte 1: carrega cues SEM style "Signs" (title cards, placas)
            # que confundem o matcher. Transcript principal mantém essas.
            if self.subtitle_path:
                cues_no_signs = subtitle.load_cues_for_matcher(
                    self.subtitle_path, exclude_signs=True,
                )
            else:
                cues_no_signs = self.cues

            # Parte 2: detecta regiões de OP/ED (estilo ou gap)
            regions = []
            if self.subtitle_path:
                regions.extend(subtitle.detect_op_ed_regions_by_style(self.subtitle_path))
            regions.extend(subtitle.detect_music_gaps(
                cues_no_signs, mkv_duration=mkv_dur,
                scene_changes=scenes,
            ))

            if regions:
                regions = sorted(set((round(a, 2), round(b, 2)) for a, b in regions))

            if regions:
                cues_for_match = subtitle.filter_cues_outside_regions(cues_no_signs, regions)
                regions_str = ", ".join(f"{a:.0f}-{b:.0f}s" for a, b in regions)
                self.after(
                    0, self.log,
                    f"🚫 Região bloqueada: {regions_str} — "
                    f"{len(self.cues)} → {len(cues_for_match)} cues "
                    f"(signs excluído + OP/ED)",
                )
                # IMPORTANTE: filtrar AD cues também. Sem isso, o LLM podia
                # escolher cue [VISUAL] que cai dentro da OP/ED — e víamos
                # cenas tipo title card "EPISÓDIO 5" (dentro do ED) sendo
                # selecionadas pra beats narrativos.
                if self.ad_cues:
                    n_ad_before = len(self.ad_cues)
                    self.ad_cues = subtitle.filter_cues_outside_regions(
                        self.ad_cues, regions,
                    )
                    if n_ad_before != len(self.ad_cues):
                        self.after(
                            0, self.log,
                            f"🚫 AD cues também filtradas: "
                            f"{n_ad_before} → {len(self.ad_cues)}"
                        )
            else:
                cues_for_match = cues_no_signs
                self.after(
                    0, self.log,
                    f"ℹ️ Sem OP/ED detectado — {len(self.cues)} → {len(cues_for_match)} cues (signs excluído)",
                )

            # 3. Matcher LLM (via non-stream, com cache)
            # Cache key inclui um digest das AD cues — se o usuário marcar/
            # desmarcar AD entre runs, o plano precisa ser regenerado.
            ad_signature = ""
            if self.ad_cues:
                ad_signature = f"ad:{len(self.ad_cues)}:" \
                    + (self.ad_cues[0].text[:40] if self.ad_cues else "")
            # Cache key usa VERSÃO SEMÂNTICA do prompt (não o conteúdo
            # literal). Bumpar MATCHER_PROMPT_VERSION quando a mudança
            # no prompt deve forçar regen. Edits cosméticos NÃO invalidam.
            cache_parts = [
                "matcher", matcher.MATCHER_PROMPT_VERSION,
                self.selected_model,
                self.short_script_text,
                self.summary_text,
                str(len(self.cues)),
                str(len(beats)),
                ad_signature,
            ]

            llm_output = None
            if self.cfg.get("use_cache"):
                llm_output = cache.get_llm(cache_parts)
                if llm_output:
                    self.after(
                        0, self.log,
                        f"🗂️ [cache HIT] Plano ({matcher.MATCHER_PROMPT_VERSION}) "
                        "— instantâneo."
                    )

            if not llm_output:
                # Glossário visual — pré-passada que mapeia termos do roteiro
                # com aliases do AD (ex: "feiticeiro mascarado" no roteiro
                # ↔ "vendedor de livros com olho grande" no AD). Reduz
                # alucinação do matcher. Cacheado por (summary, script, AD).
                visual_glossary = {}
                if self.ad_cues:
                    from core import visual_index
                    glossary_cache_parts = [
                        "visual_glossary",
                        visual_index.VISUAL_INDEX_PROMPT_VERSION,
                        self.selected_model,
                        self.summary_text,
                        self.short_script_text,
                        str(len(self.ad_cues)),
                    ]
                    cached_g = None
                    if self.cfg.get("use_cache"):
                        cached_g = cache.get_llm(glossary_cache_parts)
                    if cached_g:
                        try:
                            visual_glossary = _json.loads(cached_g)
                            self.after(0, self.log, "📚 [cache] Glossário visual carregado.")
                        except Exception:
                            visual_glossary = {}
                    if not visual_glossary:
                        self.after(0, self.log, "📚 Construindo glossário visual (roteiro ↔ AD)...")
                        visual_glossary = visual_index.build_visual_glossary(
                            summary=self.summary_text,
                            short_script=self.short_script_text,
                            ad_cues=self.ad_cues,
                            api_key=self.cfg["navy_api_key"],
                            base_url=self.cfg.get("navy_base_url") or config.DEFAULT_NAVY_BASE_URL,
                            model=self.selected_model,
                        )
                        if visual_glossary:
                            self.after(0, self.log, f"📚 Glossário com {len(visual_glossary)} entradas.")
                            if self.cfg.get("use_cache"):
                                cache.set_llm(glossary_cache_parts, _json.dumps(visual_glossary))
                if self.ad_cues:
                    self.after(
                        0, self.log,
                        f"🤖 Consultando LLM pra casar beats com cenas "
                        f"(+{len(self.ad_cues)} cues AD como dica visual)...",
                    )
                else:
                    self.after(0, self.log, "🤖 Consultando LLM pra casar beats com cenas...")
                plan = matcher.match_beats_to_cues(
                    beats=beats, cues=cues_for_match,
                    summary=self.summary_text,
                    api_key=self.cfg["navy_api_key"],
                    base_url=self.cfg.get("navy_base_url") or config.DEFAULT_NAVY_BASE_URL,
                    model=self.selected_model,
                    scene_changes=scenes,
                    pad_before=0.0,
                    max_backward_snap=3.0, max_forward_snap=0.5,
                    group_cues_gap=0.5,  # mais fino = LLM diferencia cenas de Sayu early vs late (cue-snap recupera precisão)
                    mkv_path=self.mkv_path,
                    avoid_landscape=True,
                    ad_cues=self.ad_cues or None,
                    visual_glossary=visual_glossary,
                )
                # Salva no cache o JSON serializado (simplificado)
                if self.cfg.get("use_cache"):
                    # Não guardo cue_id pq cue pode ser grupo (não bate com self.cues).
                    # video_start/end já traz a info temporal relevante.
                    serial = _json.dumps([
                        {"beat_index": m.beat.index,
                         "video_start": m.video_start, "video_end": m.video_end,
                         "why": m.why, "snapped": m.snapped}
                        for m in plan
                    ])
                    cache.set_llm(cache_parts, serial)
            else:
                # Reconstrói SceneMatch list do cache. Note: guardamos video_start
                # já snappado; `cue` do cache referencia por timestamp aproximado.
                try:
                    items = _json.loads(llm_output)
                    plan = []
                    for item in items:
                        bi = int(item["beat_index"])
                        b = next((x for x in beats if x.index == bi), None)
                        if not b:
                            continue
                        # Acha cue original mais próxima do video_start cacheado
                        vs = float(item["video_start"])
                        closest_cue = min(
                            self.cues, key=lambda c: abs(c.start - vs),
                        ) if self.cues else None
                        plan.append(matcher.SceneMatch(
                            beat=b, cue=closest_cue,
                            video_start=vs,
                            video_end=float(item["video_end"]),
                            snapped=bool(item.get("snapped")),
                            why=str(item.get("why") or ""),
                        ))
                except Exception as e:
                    self.after(0, self.log, f"[AVISO] Cache corrompido, refazendo: {e}")
                    plan = matcher.match_beats_to_cues(
                        beats=beats, cues=cues_for_match,
                        summary=self.summary_text,
                        api_key=self.cfg["navy_api_key"],
                        base_url=self.cfg.get("navy_base_url") or config.DEFAULT_NAVY_BASE_URL,
                        model=self.selected_model,
                        scene_changes=scenes,
                        max_backward_snap=3.0, max_forward_snap=0.5,
                        mkv_path=self.mkv_path, avoid_landscape=True,
                        ad_cues=self.ad_cues or None,
                    )

            self.scene_plan = plan

            # Auto-save: permite user fixar esse plano copiando o arquivo pra
            # override_plan.json na raiz do app.
            try:
                last_plan_path = os.path.join(self.work_dir, "last_plan.json")
                snapshot = [
                    {
                        "beat_index": m.beat.index,
                        "beat_text": m.beat.text,
                        "video_start": round(m.video_start, 3),
                        "duration": round(m.beat.duration, 3),
                        "snapped": m.snapped,
                        "why": m.why,
                    }
                    for m in plan
                ]
                with open(last_plan_path, "w", encoding="utf-8") as f:
                    _json.dump(snapshot, f, ensure_ascii=False, indent=2)
                self.after(
                    0, self.log,
                    f"💾 Snapshot do plano salvo em {last_plan_path} — "
                    f"copie pra override_plan.json na raiz do app pra travar esse plano."
                )
            except Exception as e:
                self.after(0, self.log, f"[AVISO] Não consegui salvar snapshot: {e}")

            self._finish_plan_and_render(plan, scenes=scenes)
            return

        except Exception as e:
            self.is_loading = False
            self.after(0, self._clear_loading_line)
            self.after(0, self.log, f"[ERRO] Plano falhou: {e}")
        finally:
            self.after(0, lambda: self.btn_plano.configure(state="normal"))
            self.after(0, lambda: self.btn_clear.configure(state="normal"))

    def _finish_plan_and_render(self, plan, scenes=None):
        """Parte final do pipeline: log do plano + cut + captions + render.
        Extraído de _call_plan pra poder ser chamado do caminho override também.

        `scenes`: lista de timestamps de scene changes (segundos). Usado pelo
        cut_clips pra decidir multi-cut (sub-clipes por beat).
        """
        try:
            # Log do plano (inserção instantânea)
            self.after(0, lambda: self._prepare_log_section("--- PLANO DE CORTES ---"))
            snapped_count = sum(1 for m in plan if m.snapped)
            total_dur = sum(m.beat.duration for m in plan)
            summary_line = (
                f"\n> {len(plan)} clipes · {total_dur:.1f}s total · "
                f"{snapped_count} com snap de cena\n"
            )
            self.after(0, self._safe_insert, summary_line)
            for m in plan:
                self.after(0, self._safe_insert, f"> {m.label()}\n")

            # Fase 3b: cortar cada beat em um mp4 silencioso
            clips_dir = os.path.join(self.work_dir, "clips")
            # Limpa clipes antigos pra não misturar runs
            if os.path.isdir(clips_dir):
                _shutil.rmtree(clips_dir, ignore_errors=True)

            self.after(0, self.log, f"🎬 Cortando {len(plan)} clipes do .mkv...")
            clip_paths = video.cut_clips(
                mkv_path=self.mkv_path,
                plan=plan,
                out_dir=clips_dir,
                binaries_dir=self.cfg.get("binaries_dir", ""),
                scene_changes=scenes or [],
                subclip_target_duration=float(
                    self.cfg.get("subclip_target_duration", 2.0)
                ),
            )

            # 6. Captions word-by-word — posição vertical configurável no ⚙️
            captions_path = os.path.join(self.work_dir, "captions.ass")
            captions.generate_ass(
                alignment=self.last_narration.alignment,
                output_path=captions_path,
                resolution=(1080, 1920),
                fontsize=110, outline=7,
                vertical_pct=self.cfg.get("captions_vertical_pct", 0.40),
            )
            self.after(0, self.log, "📝 Captions word-by-word geradas.")

            # 7. Fase 3c: render final 9:16 + blur + burn captions + mux narração + (música)
            # Resolve trilha sonora baseado no config: random/fixed/none
            music_path = music.pick_for_render(
                mode=self.cfg.get("music_mode", "random"),
                fixed_track=self.cfg.get("music_fixed_track", ""),
            )
            if music_path:
                from core.music import display_name
                vol_db = self.cfg.get("music_volume_db", -20.0)
                self.after(
                    0, self.log,
                    f"🎵 Trilha: {display_name(music_path)} ({vol_db:+.0f}dB)"
                )
            else:
                self.after(0, self.log, "🎵 Sem trilha sonora.")

            self.after(0, self.log, "🎞️ Montando short final (9:16 + blur + captions)...")
            final_path = os.path.join(self.work_dir, "short_final.mp4")
            video.render_short(
                clip_paths=clip_paths,
                narration_path=self.last_narration.audio_path,
                captions_path=captions_path,
                output_path=final_path,
                resolution=(1080, 1920),
                fg_scale=1.50,   # 50% maior que a tela; corta ~17% de cada lateral
                binaries_dir=self.cfg.get("binaries_dir", ""),
                music_path=music_path,
                music_volume_db=self.cfg.get("music_volume_db", -20.0),
            )

            size_mb = os.path.getsize(final_path) / (1024 * 1024)
            self.after(
                0, self.log,
                f"✅ Short pronto! {size_mb:.1f}MB — {final_path}",
            )
            try:
                if os.name == "nt":
                    os.startfile(final_path)  # type: ignore[attr-defined]
            except Exception:
                pass

        except Exception as e:
            self.is_loading = False
            self.after(0, self._clear_loading_line)
            self.after(0, self.log, f"[ERRO] Plano falhou: {e}")
        finally:
            self.after(0, lambda: self.btn_plano.configure(state="normal"))
            self.after(0, lambda: self.btn_clear.configure(state="normal"))

    def _prepare_log_section(self, header: str):
        self.log_box.configure(state="normal")
        line_count = int(self.log_box.index("end-1c").split(".")[0])
        self.log_box.delete(f"{line_count}.0", "end")
        self.log_box.insert("end", f"\n\n{header}\n")
        self.log_box.configure(state="disabled")

    def _copy_to_clipboard(self):
        """Copia o artefato mais avançado disponível: short > summary > transcript."""
        if self.short_script_text:
            label, text = "Roteiro Short", self.short_script_text
        elif self.summary_text:
            label, text = "Resumo", self.summary_text
        elif self.transcript_text:
            label, text = "Transcript", self.transcript_text
        else:
            return

        self.clipboard_clear()
        self.clipboard_append(text)

        original_color = self.btn_copy.cget("fg_color")
        self.btn_copy.configure(fg_color=style.BTN_SUCCESS_FG)
        self.log(f"[{label}] copiado para a área de transferência!")
        self.after(1500, lambda: self.btn_copy.configure(fg_color=original_color))
