"""Editor visual do plano de cortes — revisar e ajustar antes de renderizar.

Fecha o loop de feedback do usuário: em vez de "gera plano → renderiza no
escuro → assiste → refaz", o plano aparece como uma lista de cards com
thumbnail real de cada beat. Clicar num card abre um seletor de cenas
candidatas (scene changes vizinhas + runner-up do matcher, quando existe).
O render só dispara quando o usuário clica 🎬 Renderizar.

Beats com confidence baixa do matcher (1-2) ganham borda vermelha — é onde
o olho do usuário deve ir primeiro.
"""
import os
import queue
import threading

import customtkinter as ctk

from core import thumbs
from core.beat_archetypes import classify_beats
from ui import style
from utils.paths import resource_path

try:
    from PIL import Image
    _HAS_PIL = True
except Exception:
    _HAS_PIL = False


_ARCHETYPE_COLORS = {
    "HOOK": "#e74c3c",
    "SETUP": "#3498db",
    "ESCALADA": "#e67e22",
    "CLIMAX": "#9b59b6",
    "PAYOFF": "#27ae60",
}

_CARD_THUMB = (176, 99)     # 16:9
_PICKER_THUMB = (208, 117)  # 16:9

_BORDER_DEFAULT = "#3a3a3a"
_BORDER_EDITED = "#27ae60"
_BORDER_LOW_CONF = "#e74c3c"
_BORDER_CURRENT = "#27ae60"


def _fmt_t(t: float) -> str:
    m = int(t // 60)
    s = t - m * 60
    return f"{m:02d}:{s:05.2f}"


class _ThumbLoader:
    """Pool de threads que extrai thumbnails em background e entrega o
    resultado na thread da UI via widget.after. Tolerante a janela fechada.
    """

    def __init__(self, widget, mkv_path: str, binaries_dir: str, workers: int = 3):
        self._widget = widget
        self._mkv = mkv_path
        self._bin = binaries_dir
        self._q: queue.Queue = queue.Queue()
        self.closed = False
        for _ in range(workers):
            t = threading.Thread(target=self._work, daemon=True)
            t.start()

    def submit(self, t: float, callback):
        """callback(path_or_None) é chamado na thread da UI."""
        self._q.put((t, callback))

    def close(self):
        self.closed = True

    def _work(self):
        while not self.closed:
            try:
                t, cb = self._q.get(timeout=0.4)
            except queue.Empty:
                continue
            path = thumbs.extract_thumb(self._mkv, t, self._bin)
            if self.closed:
                return
            try:
                self._widget.after(0, cb, path)
            except Exception:
                return  # janela destruída


class PlanEditor(ctk.CTkToplevel):
    def __init__(
        self,
        parent,
        plan,                  # List[SceneMatch] — mutado in place
        scenes,                # List[float] — scene changes do mkv
        mkv_path: str,
        binaries_dir: str = "",
        on_render=None,        # callback(plan) quando usuário clica Renderizar
        on_save_override=None, # callback(plan) quando usuário fixa o plano
    ):
        super().__init__(parent)
        self._matches = plan
        self._scenes = sorted(scenes or [])
        self._mkv = mkv_path
        self._on_render = on_render
        self._on_save_override = on_save_override
        self._edited: set = set()
        self._images: dict = {}     # refs pra impedir GC dos CTkImage
        self._cards: dict = {}      # idx -> widgets do card
        self._active_picker = None  # só 1 picker aberto por vez

        # Arquétipos recalculados localmente (sem LLM) pros chips coloridos
        beats = [m.beat for m in plan]
        self._archetypes = {
            a.beat_index: a.archetype for a in classify_beats(beats)
        }

        self.title("Revisar plano de cortes")
        self.geometry("1080x700")
        self.minsize(860, 520)
        self.transient(parent)
        try:
            self.grab_set()
        except Exception:
            pass
        try:
            self.iconbitmap(resource_path("AniRecap_icon.ico"))
        except Exception:
            pass

        self._loader = _ThumbLoader(self, mkv_path, binaries_dir)
        self.protocol("WM_DELETE_WINDOW", self._cancel)

        self._build_layout()
        self._queue_all_thumbs()

    # ---------------------------------------------------------------- layout
    def _build_layout(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 6))

        ctk.CTkLabel(
            header, text="🎬 Plano de cortes", font=style.FONT_TITLE,
        ).pack(side="left")

        self._summary_lbl = ctk.CTkLabel(
            header, text="", font=style.FONT_LABEL, text_color="#999",
        )
        self._summary_lbl.pack(side="left", padx=(14, 0))

        hint = ctk.CTkLabel(
            header,
            text="clique no thumbnail pra trocar a cena",
            font=style.FONT_SMALL, text_color="#777",
        )
        hint.pack(side="right")

        self._scroll = ctk.CTkScrollableFrame(self, fg_color=style.BG_DARK)
        self._scroll.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 8))
        self._scroll.grid_columnconfigure(0, weight=1)

        for i, m in enumerate(self._matches):
            self._build_card(i, m)

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=2, column=0, sticky="ew", padx=16, pady=(4, 14))

        self._btn_override = ctk.CTkButton(
            footer, text="💾 Fixar plano (override)",
            command=self._save_override,
            fg_color=style.BTN_DEFAULT_FG, hover_color=style.BTN_DEFAULT_HOVER,
            font=style.FONT_BTN_SECONDARY, width=190, height=36,
        )
        if self._on_save_override:
            self._btn_override.pack(side="left")

        ctk.CTkButton(
            footer, text="🎬 Renderizar", command=self._render,
            fg_color=style.BTN_SUCCESS_FG, hover_color=style.BTN_SUCCESS_HOVER,
            font=style.FONT_BTN, width=180, height=36,
        ).pack(side="right")

        ctk.CTkButton(
            footer, text="Cancelar", command=self._cancel,
            fg_color=style.BTN_DANGER_FG, hover_color=style.BTN_DANGER_HOVER,
            font=style.FONT_BTN_SECONDARY, width=110, height=36,
        ).pack(side="right", padx=(0, 10))

        self._update_summary()

    def _build_card(self, idx: int, m):
        card = ctk.CTkFrame(
            self._scroll, fg_color=style.SURFACE,
            border_width=2, border_color=self._card_border(idx),
            corner_radius=8,
        )
        card.grid(row=idx, column=0, sticky="ew", pady=4, padx=2)
        card.grid_columnconfigure(1, weight=1)

        # --- thumbnail (clicável) ---
        thumb = ctk.CTkLabel(
            card, text="…", width=_CARD_THUMB[0], height=_CARD_THUMB[1],
            fg_color="#101010", corner_radius=6, font=style.FONT_LABEL,
            text_color="#555",
        )
        thumb.grid(row=0, column=0, rowspan=3, padx=(10, 12), pady=10)
        thumb.bind("<Button-1>", lambda _e, i=idx: self._open_picker(i))
        thumb.configure(cursor="hand2")

        # --- linha 1: beat número + arquétipo + duração + confidence ---
        line1 = ctk.CTkFrame(card, fg_color="transparent")
        line1.grid(row=0, column=1, sticky="w", pady=(10, 0))

        ctk.CTkLabel(
            line1, text=f"beat {m.beat.index:02d}",
            font=style.FONT_BTN_SECONDARY,
        ).pack(side="left")

        arch = self._archetypes.get(m.beat.index, "ESCALADA")
        ctk.CTkLabel(
            line1, text=f" {arch} ",
            fg_color=_ARCHETYPE_COLORS.get(arch, "#555"),
            text_color="white", corner_radius=5, font=style.FONT_SMALL,
        ).pack(side="left", padx=(8, 0))

        ctk.CTkLabel(
            line1, text=f"{m.beat.duration:.1f}s",
            font=style.FONT_SMALL, text_color="#888",
        ).pack(side="left", padx=(8, 0))

        conf = getattr(m, "confidence", 0)
        if conf:
            conf_color = "#e74c3c" if conf <= 2 else ("#e67e22" if conf == 3 else "#27ae60")
            conf_text = f"⚠ confiança {conf}/5" if conf <= 2 else f"confiança {conf}/5"
            ctk.CTkLabel(
                line1, text=conf_text, font=style.FONT_SMALL,
                text_color=conf_color,
            ).pack(side="left", padx=(8, 0))

        # --- linha 2: texto do beat ---
        ctk.CTkLabel(
            card, text=f"“{m.beat.text}”", font=style.FONT_LABEL,
            wraplength=640, justify="left", anchor="w",
        ).grid(row=1, column=1, sticky="w", pady=(2, 0))

        # --- linha 3: posição no mkv + why ---
        time_lbl = ctk.CTkLabel(
            card, text=self._time_text(m), font=style.FONT_SMALL,
            text_color="#8a8a8a", wraplength=640, justify="left", anchor="w",
        )
        time_lbl.grid(row=2, column=1, sticky="w", pady=(2, 10))

        btn = ctk.CTkButton(
            card, text="🔄 Trocar", width=90, height=30,
            command=lambda i=idx: self._open_picker(i),
            fg_color=style.BTN_DEFAULT_FG, hover_color=style.BTN_DEFAULT_HOVER,
            font=style.FONT_SMALL,
        )
        btn.grid(row=0, column=2, rowspan=3, padx=(6, 12))

        self._cards[idx] = {"card": card, "thumb": thumb, "time": time_lbl}

    # ------------------------------------------------------------- thumbnails
    def _queue_all_thumbs(self):
        if not _HAS_PIL:
            return
        for i, m in enumerate(self._matches):
            self._queue_thumb(i, m.video_start)

    def _queue_thumb(self, idx: int, t: float):
        def _apply(path):
            self._set_thumb_image(self._cards[idx]["thumb"], path, _CARD_THUMB, key=f"card{idx}")
        self._loader.submit(t, _apply)

    def _set_thumb_image(self, label, path, size, key):
        if self._loader.closed:
            return
        try:
            if path and _HAS_PIL:
                img = ctk.CTkImage(Image.open(path), size=size)
                self._images[key] = img
                label.configure(image=img, text="")
            else:
                label.configure(text="sem preview")
        except Exception:
            # Falha visível > falha silenciosa: sem isso, um erro de
            # CTkImage (ex: PIL.ImageTk fora do bundle) deixava o card
            # em "…" pra sempre sem nenhuma pista.
            try:
                label.configure(text="⚠ erro img")
            except Exception:
                pass  # janela fechada no meio do load

    # ------------------------------------------------------------ estado/UI
    def _card_border(self, idx: int) -> str:
        if idx in self._edited:
            return _BORDER_EDITED
        conf = getattr(self._matches[idx], "confidence", 0)
        if 1 <= conf <= 2:
            return _BORDER_LOW_CONF
        return _BORDER_DEFAULT

    def _time_text(self, m) -> str:
        why = (m.why or "").strip()
        base = f"mkv {_fmt_t(m.video_start)} → {_fmt_t(m.video_end)}"
        cue_txt = ""
        if m.cue is not None and m.cue.text:
            snippet = m.cue.text[:70].replace("\n", " ")
            cue_txt = f"  ·  cue: “{snippet}”"
        return f"{base}  ·  {why}{cue_txt}" if why else f"{base}{cue_txt}"

    def _update_summary(self):
        total = sum(m.beat.duration for m in self._matches)
        edited = len(self._edited)
        txt = f"{len(self._matches)} clipes · {total:.1f}s"
        if edited:
            txt += f" · {edited} editado(s)"
        low_conf = sum(
            1 for m in self._matches if 1 <= getattr(m, "confidence", 0) <= 2
        )
        if low_conf:
            txt += f" · ⚠ {low_conf} com confiança baixa"
        self._summary_lbl.configure(text=txt)

    def _refresh_card(self, idx: int):
        m = self._matches[idx]
        w = self._cards[idx]
        w["card"].configure(border_color=self._card_border(idx))
        w["time"].configure(text=self._time_text(m))
        w["thumb"].configure(image=None, text="…")
        self._images.pop(f"card{idx}", None)
        self._queue_thumb(idx, m.video_start)

    # ----------------------------------------------------- seletor de cenas
    def _candidates_for(self, m) -> list:
        """Timestamps candidatos pro beat: scene changes vizinhas (±45s),
        runner-up do matcher (quando existe) e a posição atual.
        Cap de 24, os mais próximos da posição atual.
        """
        cur = m.video_start
        cands = {round(cur, 2)}

        runner = getattr(m, "runner_up_start", -1.0)
        if runner is not None and runner >= 0:
            cands.add(round(runner, 2))

        if self._scenes:
            near = [s for s in self._scenes if abs(s - cur) <= 45.0]
            near.sort(key=lambda s: abs(s - cur))
            for s in near[:24]:
                cands.add(round(s, 2))
        else:
            for d in (-4.0, -2.0, -1.0, 1.0, 2.0, 4.0):
                cands.add(round(max(0.0, cur + d), 2))

        return sorted(cands)

    def _open_picker(self, idx: int):
        # Fecha picker anterior se ainda estiver aberto (evita 2 grabs)
        if self._active_picker is not None:
            try:
                self._active_picker.destroy()
            except Exception:
                pass
            self._active_picker = None

        m = self._matches[idx]
        cur = round(m.video_start, 2)
        runner = getattr(m, "runner_up_start", -1.0)
        runner = round(runner, 2) if (runner is not None and runner >= 0) else None

        win = ctk.CTkToplevel(self)
        self._active_picker = win
        win.title(f"Beat {m.beat.index:02d} — trocar cena")
        win.geometry("980x640")
        win.minsize(760, 480)
        win.transient(self)
        try:
            win.grab_set()
        except Exception:
            pass

        ctk.CTkLabel(
            win, text=f"“{m.beat.text}”", font=style.FONT_LABEL,
            wraplength=920, justify="left",
        ).pack(padx=16, pady=(14, 2), anchor="w")

        ctk.CTkLabel(
            win,
            text="Escolha o momento do episódio que esse beat deve mostrar "
                 "(borda verde = atual, 🥈 = 2ª opção do matcher):",
            font=style.FONT_SMALL, text_color="#888",
        ).pack(padx=16, pady=(0, 8), anchor="w")

        grid = ctk.CTkScrollableFrame(win, fg_color=style.BG_DARK)
        grid.pack(fill="both", expand=True, padx=16, pady=(0, 10))

        cols = 4
        for c in range(cols):
            grid.grid_columnconfigure(c, weight=1)

        cands = self._candidates_for(m)
        for j, t in enumerate(cands):
            is_cur = abs(t - cur) < 0.05
            is_runner = runner is not None and abs(t - runner) < 0.05

            cell = ctk.CTkFrame(
                grid, fg_color=style.SURFACE, corner_radius=8,
                border_width=2,
                border_color=_BORDER_CURRENT if is_cur else _BORDER_DEFAULT,
            )
            cell.grid(row=j // cols, column=j % cols, padx=5, pady=5, sticky="n")

            lbl = ctk.CTkLabel(
                cell, text="…", width=_PICKER_THUMB[0], height=_PICKER_THUMB[1],
                fg_color="#101010", corner_radius=6, font=style.FONT_LABEL,
                text_color="#555",
            )
            lbl.pack(padx=6, pady=(6, 2))
            lbl.configure(cursor="hand2")

            tag = ""
            if is_cur:
                tag = " · atual"
            elif is_runner:
                tag = " · 🥈 opção B"
            ctk.CTkLabel(
                cell, text=f"{_fmt_t(t)}{tag}", font=style.FONT_SMALL,
                text_color="#27ae60" if is_cur else ("#f1c40f" if is_runner else "#aaa"),
            ).pack(pady=(0, 6))

            if not is_cur:
                lbl.bind(
                    "<Button-1>",
                    lambda _e, i=idx, ts=t, w=win: self._apply_choice(i, ts, w),
                )
                cell.configure(cursor="hand2")

            key = f"pick{idx}_{j}"
            self._loader.submit(
                t,
                lambda p, l=lbl, k=key: self._set_thumb_image(l, p, _PICKER_THUMB, k),
            )

        ctk.CTkButton(
            win, text="Fechar", command=win.destroy,
            fg_color=style.BTN_DEFAULT_FG, hover_color=style.BTN_DEFAULT_HOVER,
            font=style.FONT_BTN_SECONDARY, width=110, height=32,
        ).pack(pady=(0, 12))

    def _apply_choice(self, idx: int, new_start: float, picker):
        m = self._matches[idx]
        m.video_start = new_start
        m.video_end = new_start + m.beat.duration
        m.snapped = True
        why = (m.why or "").strip()
        if not why.startswith("[manual]"):
            m.why = f"[manual] {why}" if why else "[manual]"
        self._edited.add(idx)
        try:
            picker.destroy()
        except Exception:
            pass
        if picker is self._active_picker:
            self._active_picker = None
        self._refresh_card(idx)
        self._update_summary()

    # ---------------------------------------------------------------- ações
    def _save_override(self):
        if not self._on_save_override:
            return
        try:
            self._on_save_override(self._matches)
            self._btn_override.configure(text="✅ Plano fixado")
            self.after(
                2000,
                lambda: self._btn_override.configure(text="💾 Fixar plano (override)"),
            )
        except Exception:
            pass

    def _render(self):
        self._loader.close()
        cb = self._on_render
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()
        if cb:
            cb(self._matches)

    def _cancel(self):
        self._loader.close()
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()


def open_plan_editor(
    parent, plan, scenes, mkv_path,
    binaries_dir="", on_render=None, on_save_override=None,
) -> PlanEditor:
    """Abre o editor. Deve ser chamado na thread da UI (via parent.after)."""
    return PlanEditor(
        parent, plan, scenes, mkv_path,
        binaries_dir=binaries_dir,
        on_render=on_render,
        on_save_override=on_save_override,
    )
