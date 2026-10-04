"""Widgets propios de la ventana: tarjetas, botones e interruptores."""
from __future__ import annotations

import tkinter as tk

from ui.app_base import COLORS, _widget_bg


try:
    from PIL import Image, ImageDraw, ImageTk
    _PIL_OK = True
except Exception:
    _PIL_OK = False


class ToolTip:
    """Simple hover tooltip for any Tkinter widget."""

    def __init__(self, widget: tk.Widget, text: str) -> None:
        self._widget = widget
        self._text = text
        self._tip: tk.Toplevel | None = None
        widget.bind("<Enter>", self._show)
        widget.bind("<Leave>", self._hide)

    def _show(self, event: tk.Event | None = None) -> None:
        if self._tip or not self._text:
            return
        x = self._widget.winfo_rootx() + 20
        y = self._widget.winfo_rooty() + self._widget.winfo_height() + 4
        self._tip = tk.Toplevel(self._widget)
        self._tip.wm_overrideredirect(True)
        self._tip.wm_geometry(f"+{x}+{y}")
        lbl = tk.Label(
            self._tip,
            text=self._text,
            justify="left",
            background="#1e293b",
            foreground="#e2e8f0",
            relief="flat",
            font=("Segoe UI", 9),
            wraplength=340,
            padx=8,
            pady=5,
        )
        lbl.pack()

    def _hide(self, event: tk.Event | None = None) -> None:
        if self._tip:
            self._tip.destroy()
            self._tip = None


class _CornerImageCache:
    """Cache anti-aliased rounded corner PhotoImages keyed by visual params."""
    _store: dict = {}

    @classmethod
    def get(cls, radius: int, card_bg: str, border: str, border_w: int,
            parent_bg: str, corner: str):
        if not _PIL_OK:
            return None
        key = (radius, card_bg, border, border_w, parent_bg, corner)
        cached = cls._store.get(key)
        if cached is not None:
            return cached
        ss = 4
        s = radius * ss
        bw = max(0, border_w * ss)
        img = Image.new("RGB", (s, s), parent_bg)
        draw = ImageDraw.Draw(img)
        # bbox de la elipse (2x el lado) anclada según la esquina
        if corner == "nw":
            bbox = (0, 0, 2 * s - 1, 2 * s - 1)
            start, end = 180, 270
        elif corner == "ne":
            bbox = (-s, 0, s - 1, 2 * s - 1)
            start, end = 270, 360
        elif corner == "sw":
            bbox = (0, -s, 2 * s - 1, s - 1)
            start, end = 90, 180
        else:  # se
            bbox = (-s, -s, s - 1, s - 1)
            start, end = 0, 90
        draw.ellipse(bbox, fill=card_bg)
        if bw > 0:
            draw.arc(bbox, start, end, fill=border, width=bw)
        img = img.resize((radius, radius), Image.LANCZOS)
        photo = ImageTk.PhotoImage(img)
        cls._store[key] = photo
        return photo


class _ButtonImageCache:
    """Cache anti-aliased rounded button background images."""
    _store: dict = {}

    @classmethod
    def get(cls, width: int, height: int, radius: int, fill: str,
            border: str, border_w: int, parent_bg: str):
        if not _PIL_OK or width <= 0 or height <= 0:
            return None
        key = (width, height, radius, fill, border, border_w, parent_bg)
        cached = cls._store.get(key)
        if cached is not None:
            return cached
        ss = 2
        w, h = width * ss, height * ss
        r = max(1, radius * ss)
        bw = max(0, border_w * ss)
        img = Image.new("RGB", (w, h), parent_bg)
        draw = ImageDraw.Draw(img)
        # rounded_rectangle existe desde Pillow 8.2
        try:
            draw.rounded_rectangle(
                (0, 0, w - 1, h - 1),
                radius=r, fill=fill,
                outline=(border if bw > 0 else None),
                width=bw,
            )
        except AttributeError:
            # Fallback ellipse+rect para Pillow viejo
            draw.ellipse((0, 0, 2 * r, 2 * r), fill=fill)
            draw.ellipse((w - 2 * r - 1, 0, w - 1, 2 * r), fill=fill)
            draw.ellipse((0, h - 2 * r - 1, 2 * r, h - 1), fill=fill)
            draw.ellipse((w - 2 * r - 1, h - 2 * r - 1, w - 1, h - 1), fill=fill)
            draw.rectangle((r, 0, w - r - 1, h - 1), fill=fill)
            draw.rectangle((0, r, w - 1, h - r - 1), fill=fill)
        img = img.resize((width, height), Image.LANCZOS)
        photo = ImageTk.PhotoImage(img)
        cls._store[key] = photo
        return photo


class RoundedCard(tk.Frame):
    """Frame with simulated rounded corners and a thin border."""

    def __init__(self, parent, *, radius: int = 12, bg: str | None = None,
                 border: str | None = None, parent_bg: str | None = None, **kw):
        bg = bg or COLORS["panel"]
        border = border or COLORS["border"]
        super().__init__(parent, bg=bg, **kw)
        self._radius = radius
        self._card_bg = bg
        self._border_color = border
        self._parent_bg = parent_bg or _widget_bg(parent)
        self._overlays: list[tk.Widget] = []
        self.bind("<Configure>", self._on_resize)
        self.after_idle(self._setup_rounding)

    def _setup_rounding(self) -> None:
        for w in self._overlays:
            try:
                w.destroy()
            except tk.TclError:
                pass
        self._overlays = []
        # Bordes rectos a lo largo de toda la card: los extremos quedan
        # ocultos por las esquinas (que se elevan al frente) y así no
        # se ve ningún corte/gap entre línea recta y curva.
        top = tk.Frame(self, bg=self._border_color, height=1)
        top.place(x=0, y=0, relwidth=1.0)
        bottom = tk.Frame(self, bg=self._border_color, height=1)
        bottom.place(x=0, rely=1.0, y=-1, relwidth=1.0)
        left = tk.Frame(self, bg=self._border_color, width=1)
        left.place(x=0, y=0, relheight=1.0)
        right = tk.Frame(self, bg=self._border_color, width=1)
        right.place(relx=1.0, x=-1, y=0, relheight=1.0)
        self._overlays.extend([top, bottom, left, right])
        # Esquinas redondeadas
        for anchor in ("nw", "ne", "sw", "se"):
            self._overlays.append(self._make_corner(anchor))
        self._raise_overlays()

    def _make_corner(self, anchor: str) -> tk.Widget:
        r = self._radius
        # Intenta con Pillow (anti-aliasing)
        photo = _CornerImageCache.get(r, self._card_bg, self._border_color, 1,
                                      self._parent_bg, anchor)
        if photo is not None:
            lbl = tk.Label(self, image=photo, bg=self._parent_bg, borderwidth=0,
                           highlightthickness=0)
            lbl.image = photo  # keep ref
            self._place_corner(lbl, anchor)
            return lbl
        # Fallback: dibujo con Canvas (sin AA)
        canvas = tk.Canvas(self, width=r, height=r, bg=self._parent_bg,
                           highlightthickness=0, borderwidth=0)
        if anchor == "nw":
            bbox, start = (0, 0, 2 * r, 2 * r), 90
        elif anchor == "ne":
            bbox, start = (-r, 0, r, 2 * r), 0
        elif anchor == "sw":
            bbox, start = (0, -r, 2 * r, r), 180
        else:
            bbox, start = (-r, -r, r, r), 270
        canvas.create_arc(*bbox, start=start, extent=90,
                          fill=self._card_bg, outline="", style="pieslice")
        canvas.create_arc(*bbox, start=start, extent=90,
                          outline=self._border_color, width=1, style="arc")
        self._place_corner(canvas, anchor)
        return canvas

    def _place_corner(self, w: tk.Widget, anchor: str) -> None:
        r = self._radius
        if anchor == "nw":
            w.place(x=0, y=0)
        elif anchor == "ne":
            w.place(relx=1.0, x=-r, y=0)
        elif anchor == "sw":
            w.place(x=0, rely=1.0, y=-r)
        else:
            w.place(relx=1.0, x=-r, rely=1.0, y=-r)

    def _raise_overlays(self) -> None:
        # Usa el comando Tcl directo para evitar el alias Canvas.lift = tag_raise en Python 3.x
        for w in self._overlays:
            try:
                self.tk.call("raise", w._w)
            except tk.TclError:
                pass

    def _on_resize(self, _event=None) -> None:
        self._raise_overlays()


class RoundedButton(tk.Canvas):
    """Canvas-based button with rounded corners + hover."""

    def __init__(self, parent, *, text: str = "", command=None,
                 bg: str = "#006c49", fg: str = "#ffffff",
                 hover_bg: str | None = None, hover_fg: str | None = None,
                 disabled_bg: str = "#8ba59c", disabled_fg: str = "#ffffff",
                 border: str | None = None, parent_bg: str | None = None,
                 font=("Segoe UI", 10, "bold"), radius: int = 10,
                 padx: int = 14, pady: int = 10, width: int | None = None,
                 height: int | None = None, anchor: str = "center"):
        parent_bg = parent_bg or _widget_bg(parent)
        super().__init__(parent, bg=parent_bg, highlightthickness=0, borderwidth=0, cursor="hand2")
        self._command = command
        self._bg = bg
        self._fg = fg
        self._hover_bg = hover_bg or bg
        self._hover_fg = hover_fg or fg
        self._disabled_bg = disabled_bg
        self._disabled_fg = disabled_fg
        self._border = border
        self._font = font
        self._radius = radius
        self._padx = padx
        self._pady = pady
        self._anchor = anchor
        self._text = text
        self._disabled = False
        self._rect_id = None
        self._text_id = None
        # Tamaño inicial basado en el texto
        if width is None or height is None:
            tmp = tk.Label(self, text=text, font=font)
            w = tmp.winfo_reqwidth() + padx * 2
            h = tmp.winfo_reqheight() + pady * 2
            tmp.destroy()
            width = width or w
            height = height or h
        self.configure(width=width, height=height)
        self._draw(self._bg, self._fg)
        self.bind("<Configure>", lambda _e: self._draw(self._current_bg(), self._current_fg()))
        self.bind("<Enter>", lambda _e: self._on_enter())
        self.bind("<Leave>", lambda _e: self._on_leave())
        self.bind("<Button-1>", lambda _e: self._on_click())

    def _current_bg(self) -> str:
        return self._disabled_bg if self._disabled else self._bg

    def _current_fg(self) -> str:
        return self._disabled_fg if self._disabled else self._fg

    def _round_rect_points(self, w: int, h: int, r: int) -> list[int]:
        # Inset 1px para que el outline no se clipee en los bordes del canvas
        x0, y0, x1, y1 = 1, 1, w - 2, h - 2
        return [
            x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r,
            x1, y1 - r, x1, y1, x1 - r, y1,
            x0 + r, y1, x0, y1, x0, y1 - r,
            x0, y0 + r, x0, y0, x0, y0,
        ]

    def _draw(self, bg: str, fg: str) -> None:
        w = max(int(self.winfo_width()), 1)
        h = max(int(self.winfo_height()), 1)
        self.delete("all")
        outline = self._border or bg
        border_w = 1 if self._border else 0
        photo = _ButtonImageCache.get(w, h, self._radius, bg, outline, border_w,
                                      _widget_bg(self.master))
        if photo is not None:
            self._bg_photo = photo  # keep ref
            self.create_image(0, 0, anchor="nw", image=photo)
        else:
            points = self._round_rect_points(w, h, self._radius)
            self._rect_id = self.create_polygon(points, smooth=True, fill=bg,
                                                outline=outline, width=1)
        # Texto centrado o anclado
        if self._anchor == "w":
            x, anchor = self._padx, "w"
        elif self._anchor == "e":
            x, anchor = w - self._padx, "e"
        else:
            x, anchor = w // 2, "center"
        self._text_id = self.create_text(x, h // 2, text=self._text, fill=fg, font=self._font, anchor=anchor)

    def configure_text(self, text: str) -> None:
        self._text = text
        self._draw(self._current_bg(), self._current_fg())

    def set_disabled(self, disabled: bool) -> None:
        self._disabled = bool(disabled)
        self.configure(cursor="" if disabled else "hand2")
        self._draw(self._current_bg(), self._current_fg())

    def set_colors(self, *, bg: str | None = None, fg: str | None = None,
                   hover_bg: str | None = None, hover_fg: str | None = None) -> None:
        if bg is not None:
            self._bg = bg
        if fg is not None:
            self._fg = fg
        if hover_bg is not None:
            self._hover_bg = hover_bg
        if hover_fg is not None:
            self._hover_fg = hover_fg
        self._draw(self._current_bg(), self._current_fg())

    def _on_enter(self) -> None:
        if not self._disabled:
            self._draw(self._hover_bg, self._hover_fg)

    def _on_leave(self) -> None:
        self._draw(self._current_bg(), self._current_fg())

    def _on_click(self) -> None:
        if self._disabled:
            return
        if self._command is not None:
            try:
                self._command()
            except Exception:
                pass


class ToggleSwitch(tk.Canvas):
    """Modern iOS-style toggle switch bound to a tk.BooleanVar."""

    def __init__(self, parent, variable: tk.BooleanVar, command=None, width: int = 44, height: int = 24,
                 bg: str = "#ffffff", on_color: str = "#006c49", off_color: str = "#c5c6cd"):
        super().__init__(parent, width=width, height=height, bg=bg, highlightthickness=0, borderwidth=0, cursor="hand2")
        self._var = variable
        self._trace = None
        self._on_color = on_color
        self._off_color = off_color
        self._cmd = command
        self._sw = width
        self._sh = height
        self._track = None
        self._knob = None
        self._redraw()
        self.bind("<Button-1>", self._toggle)
        self.bind("<Destroy>", self._on_destroy, add="+")
        try:
            self._trace = variable.trace_add("write", lambda *_: self._redraw())
        except Exception:
            pass

    def _round_rect(self, x1, y1, x2, y2, r, **kwargs):
        points = [
            x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
            x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
            x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
        ]
        return self.create_polygon(points, smooth=True, **kwargs)

    def _remove_trace(self):
        if self._trace is None:
            return
        try:
            self._var.trace_remove("write", self._trace)
        except (tk.TclError, ValueError):
            pass
        self._trace = None

    def _on_destroy(self, event=None):
        if event is not None and event.widget is not self:
            return
        self._remove_trace()

    def _redraw(self):
        try:
            self.delete("all")
            on = bool(self._var.get())
            color = self._on_color if on else self._off_color
            self._round_rect(1, 1, self._sw - 1, self._sh - 1, self._sh // 2, fill=color, outline=color)
            pad = 3
            kr = self._sh - pad * 2
            kx = self._sw - pad - kr if on else pad
            self.create_oval(kx, pad, kx + kr, pad + kr, fill="#ffffff", outline="#ffffff")
        except tk.TclError as exc:
            if "invalid command name" not in str(exc):
                raise
            self._remove_trace()

    def _toggle(self, _event=None):
        try:
            self._var.set(not bool(self._var.get()))
            self._redraw()
        except tk.TclError as exc:
            if "invalid command name" not in str(exc):
                raise
            self._remove_trace()
            return
        if self._cmd is not None:
            try:
                self._cmd()
            except Exception:
                pass
