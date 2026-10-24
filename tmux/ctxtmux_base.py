#!/usr/bin/env python3
"""ctxmux: a very light tmux-like REPL over hashed pydantic contexts.

Concepts
--------
ContextParams  pydantic model (frozen). Its canonical JSON hashes to the context id.
Context        runtime object for one id (params + mutable state). Shared by every pane showing it.
Pane           a *view* on a context (own scrollback). Leaf of the layout tree.
Screen         named layout tree of panes (like a tmux window).
Command        pydantic model; fields -> argparse CLI automatically; run() does the work.

A context is "running" iff at least one pane (in any screen) displays it.
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import os
import re
import shlex
import shutil
import sys
import time
import types
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from ipaddress import IPv4Address
from pathlib import Path
from typing import ClassVar, Iterator, Literal, Union, get_args, get_origin

from pydantic import BaseModel, ConfigDict, Field, ValidationError

try:  # line editing / history if available
    import readline  # noqa: F401
except ImportError:
    pass


# ───────────────────────── pydantic model -> argparse ─────────────────────────
class ParseExit(Exception):
    """Raised instead of sys.exit() so -h / errors land in the pane."""


class _Parser(argparse.ArgumentParser):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self._msgs: list[str] = []

    def _print_message(self, message, file=None):
        if message:
            self._msgs.append(message)

    def exit(self, status=0, message=None):
        if message:
            self._msgs.append(message)
        raise ParseExit("".join(self._msgs).rstrip())

    def error(self, message):
        self.exit(2, f"error: {message}\n")


def _unwrap(ann):
    """X | None  ->  X"""
    if get_origin(ann) in (Union, types.UnionType):
        args = [a for a in get_args(ann) if a is not type(None)]
        if len(args) == 1:
            return args[0]
    return ann


def _conv(t):
    """argparse `type=` callable for annotation t (pydantic re-validates afterwards)."""
    ok = isinstance(t, type) and not issubclass(t, BaseModel)
    return t if ok else str


def fill_parser(p: argparse.ArgumentParser, model: type[BaseModel]) -> argparse.ArgumentParser:
    """Required fields -> positionals. Optional fields -> --flags. Defaults left to pydantic."""
    for name, f in model.model_fields.items():
        ann, required = _unwrap(f.annotation), f.is_required()
        origin = get_origin(ann)
        help_ = (f.description or "") + ("" if required else f" (default: {f.default})")
        flag = "--" + name.replace("_", "-")
        if ann is bool:
            p.add_argument(flag, dest=name, action=argparse.BooleanOptionalAction,
                           default=argparse.SUPPRESS, help=help_)
            continue
        kw: dict = {"help": help_ or None}
        if origin is Literal:
            kw["choices"], kw["type"] = list(get_args(ann)), str
        elif origin is list:
            inner = _unwrap(get_args(ann)[0]) if get_args(ann) else str
            kw["nargs"], kw["type"] = ("+" if required else "*"), _conv(inner)
        else:
            kw["type"] = _conv(ann)
        positional = (f.json_schema_extra or {}).get("positional") if isinstance(f.json_schema_extra, dict) else False
        if required:
            p.add_argument(name, **kw)
        elif positional and "nargs" not in kw:  # Field(..., json_schema_extra={"positional": True}) -> `cmd [value]`
            # default=None, not SUPPRESS: argparse runs type/choices checks on a *string* default, so a
            # SUPPRESS sentinel gets validated as if the user had typed it. None is skipped; see _given().
            p.add_argument(name, nargs="?", default=None, **kw)
        else:
            p.add_argument(flag, dest=name, default=argparse.SUPPRESS, **kw)
    return p


def _given(ns: argparse.Namespace) -> dict:
    """Parsed values the user actually supplied (optional positionals come back as None when absent)."""
    return {k: v for k, v in vars(ns).items() if v is not None}


class CmdError(Exception):
    pass


# ───────────────────────────── commands & contexts ────────────────────────────
class Command(BaseModel):
    name: ClassVar[str]

    @classmethod
    def parser(cls) -> argparse.ArgumentParser:
        return fill_parser(_Parser(prog=cls.name, description=cls.__doc__), cls)

    @classmethod
    def parse(cls, argv: list[str]) -> "Command":
        return cls(**_given(cls.parser().parse_args(argv)))

    def run(self, app: "App") -> None:
        raise NotImplementedError


GLOBAL: dict[str, type[Command]] = {}
KINDS: dict[str, type["ContextParams"]] = {}


def command(name: str, on: type["ContextParams"] | None = None):
    """Register a Command globally, or only inside contexts of kind `on`."""
    def deco(cls):
        cls.name = name
        (on.commands if on else GLOBAL)[name] = cls
        return cls
    return deco


class ContextParams(BaseModel):
    model_config = ConfigDict(frozen=True)
    kind: ClassVar[str]
    commands: ClassVar[dict[str, type[Command]]]

    def ctx_hash(self) -> str:
        payload = json.dumps([self.kind, self.model_dump(mode="json")], sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()[:12]

    def label(self) -> str:
        return f"{self.kind}(" + ",".join(f"{v}" for v in self.model_dump(mode="json").values()) + ")"


def context_type(kind: str):
    def deco(cls):
        cls.kind, cls.commands = kind, {}
        KINDS[kind] = cls
        return cls
    return deco


@dataclass
class Context:
    params: ContextParams
    state: dict = field(default_factory=dict)

    @property
    def id(self) -> str:
        return self.params.ctx_hash()


# ─────────────────────────────── layout tree ──────────────────────────────────
@dataclass(eq=False)
class Pane:
    id: int
    ctx: str  # context id
    buf: deque = field(default_factory=lambda: deque(maxlen=500))
    color: str | None = None  # custom border colour "#rrggbb" (wins over the palette)
    slot: int | None = None   # index into the screen's palette (so a palette switch recolours the pane)
    box: tuple[int, int, int, int] | None = None  # saved window geometry (x, y, w, h in px) for the web "modal"


@dataclass(eq=False)
class Split:
    direction: Literal["h", "v"]  # tmux semantics: h = side by side, v = stacked
    a: "Node"
    b: "Node"
    ratio: float = 0.5  # share of the space given to `a`


Node = Union[Pane, Split]


def panes(n: Node) -> Iterator[Pane]:
    if isinstance(n, Pane):
        yield n
    else:
        yield from panes(n.a)
        yield from panes(n.b)


def _replace(n: Node, target: Pane, new: Node) -> Node:
    if n is target:
        return new
    if isinstance(n, Split):
        return Split(n.direction, _replace(n.a, target, new), _replace(n.b, target, new), n.ratio)
    return n


def _remove(n: Node, target: Pane) -> Node | None:
    if n is target:
        return None
    if isinstance(n, Split):
        a, b = _remove(n.a, target), _remove(n.b, target)
        return b if a is None else a if b is None else Split(n.direction, a, b, n.ratio)
    return n


def rects(n: Node, x: int, y: int, w: int, h: int) -> Iterator[tuple[Pane, tuple[int, int, int, int]]]:
    if isinstance(n, Pane):
        yield n, (x, y, w, h)
    elif n.direction == "h":
        wa = max(1, min(w - 1, int(w * n.ratio)))
        yield from rects(n.a, x, y, wa, h)
        yield from rects(n.b, x + wa, y, w - wa, h)
    else:
        ha = max(1, min(h - 1, int(h * n.ratio)))
        yield from rects(n.a, x, y, w, ha)
        yield from rects(n.b, x, y + ha, w, h - ha)


# ───────────────────────────────── palettes ───────────────────────────────────
NSLOTS = 5
PALETTES = [  # the only palettes: 4 fixed sets of 5 colours, left-to-right order kept
    ["#80bee2", "#288eb0", "#082538", "#fca700", "#f36e00"],  # sky / teal / navy / amber / orange
    ["#08243b", "#c50f1d", "#ee6900", "#f8b231", "#e5dca6"],  # navy / red / orange / amber / sand
    ["#1f3643", "#2d8d7d", "#e1b751", "#ec8f4a", "#db573d"],  # slate / teal / mustard / apricot / coral
    ["#d81e36", "#eefaea", "#9cd2d5", "#3a688d", "#162847"],  # red / mint / aqua / steel / navy
]


def oklch_hex(L: float, C: float, hue: float) -> str:
    """OKLCH -> '#rrggbb'. Chroma is lowered until the colour fits sRGB, so hue and lightness are preserved."""
    for _ in range(40):
        a, b = C * math.cos(math.radians(hue)), C * math.sin(math.radians(hue))
        l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3
        m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3
        s = (L - 0.0894841775 * a - 1.2914855480 * b) ** 3
        rgb = (4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
               -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
               -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s)
        if all(-1e-4 <= v <= 1 + 1e-4 for v in rgb):
            break
        C *= 0.93
    enc = lambda v: 12.92 * v if v <= 0.0031308 else 1.055 * v ** (1 / 2.4) - 0.055
    return "#" + "".join("%02x" % round(255 * min(1.0, max(0.0, enc(max(v, 0.0))))) for v in rgb)


BORDER_MIN_L = 0.56  # OKLab lightness floor for *drawing* a colour on the dark UI (contrast >= ~3.5 vs the page)


def hex_to_oklch(h: str) -> tuple[float, float, float]:
    lin = lambda x: x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(int(h[i:i + 2], 16) / 255) for i in (1, 3, 5))
    l = (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b) ** (1 / 3)
    m = (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b) ** (1 / 3)
    s = (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b) ** (1 / 3)
    L = 0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s
    a = 1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s
    bb = 0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s
    return L, math.hypot(a, bb), math.degrees(math.atan2(bb, a)) % 360


def visible(h: str, min_l: float = BORDER_MIN_L) -> str:
    """The colour to *draw* for `h`: unchanged unless it is too dark for the dark UI (black, navy...), in which
    case lightness is raised and hue/chroma kept. Palettes themselves always store the exact colours."""
    L, C, H = hex_to_oklch(h)
    return h if L >= min_l else oklch_hex(min_l, C, H)


def palette_index_from_json(d) -> int:
    """Index of a saved palette. Accepts {"index"}, a bare int, or an old-format dict (matched by its colours);
    anything unknown falls back to the first palette."""
    if isinstance(d, int) and 0 <= d < len(PALETTES):
        return d
    if isinstance(d, dict):
        i = d.get("index")
        if isinstance(i, int) and 0 <= i < len(PALETTES):
            return i
        if d.get("colors") in PALETTES:
            return PALETTES.index(d["colors"])
    return 0


def clamp_box(x, y, w, h) -> tuple[int, int, int, int]:
    """Sane window geometry: on-screen origin, at least 260x140, at most 6000 px."""
    return (max(0, min(int(x), 20000)), max(0, min(int(y), 20000)),
            max(260, min(int(w), 6000)), max(140, min(int(h), 6000)))


def neighbor(root: Node, cur: Pane, direction: str) -> Pane | None:
    """Nearest pane in `direction` that overlaps `cur` on the other axis; ties -> larger overlap, then layout order."""
    rs = dict(rects(root, 0, 0, 10_000, 10_000))  # fine-grained integer canvas, ratios resolved by rects()
    cx, cy, cw, ch = rs[cur]
    horiz = direction in ("left", "right")
    best, best_key = None, None
    for p, (x, y, w, h) in rs.items():
        if p is cur:
            continue
        gap = {"right": x - (cx + cw), "left": cx - (x + w), "down": y - (cy + ch), "up": cy - (y + h)}[direction]
        a0, al, b0, bl = (cy, ch, y, h) if horiz else (cx, cw, x, w)
        overlap = min(a0 + al, b0 + bl) - max(a0, b0)
        if gap < 0 or overlap <= 0:
            continue
        key = (gap, -overlap)
        if best_key is None or key < best_key:
            best, best_key = p, key
    return best


@dataclass
class Screen:
    name: str
    root: Node
    focus: Pane
    _ids: itertools.count = field(default_factory=lambda: itertools.count(2))
    palette: int = 0  # index into PALETTES
    next_slot: int = 1  # slot handed to the next pane created here: colours cycle through the palette

    @property
    def colors(self) -> list[str]:
        return PALETTES[self.palette]

    def color_of(self, p: Pane) -> str | None:
        if p.color:
            return p.color
        return self.colors[p.slot] if p.slot is not None else None

    @classmethod
    def create(cls, name: str, ctx: str, palette: int = 0) -> "Screen":
        p = Pane(1, ctx, slot=0)
        return cls(name, p, p, palette=palette)

    def split(self, direction: str, ctx: str) -> Pane:
        new = Pane(next(self._ids), ctx, slot=self.next_slot % NSLOTS)
        self.next_slot += 1
        self.root = _replace(self.root, self.focus, Split(direction, self.focus, new))
        self.focus = new
        return new

    def close(self) -> None:
        order = list(panes(self.root))
        i = order.index(self.focus)
        self.root = _remove(self.root, self.focus)
        rest = list(panes(self.root))
        self.focus = rest[min(i, len(rest) - 1)]


def norm_color(v) -> str | None:
    """'#f80' / 'FF8800' -> '#ff8800'; anything else -> None."""
    m = re.fullmatch(r"#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})", v) if isinstance(v, str) else None
    if not m:
        return None
    h = m.group(1).lower()
    return "#" + (h if len(h) == 6 else "".join(ch * 2 for ch in h))


def snapshot_path(name: str) -> Path:
    if not re.fullmatch(r"[\w][\w.-]*", name):
        raise CmdError(f"bad snapshot name '{name}'")
    return Path(os.environ.get("CTXMUX_DIR", "~/.ctxmux")).expanduser() / f"{name}.json"


# ────────────────────────────────── the app ───────────────────────────────────
class App:
    def __init__(self):
        self.contexts: dict[str, Context] = {}
        self.screens: dict[str, Screen] = {}
        self.cur = "main"
        self.alive = True
        self.modal: int | None = None  # per-client view state, set by the web layer: pane shown in a window
        self.mini = False              # ... and whether that window is reduced to its title bar
        self.modal_screen: str | None = None  # ... and the screen it was opened on (pane ids are per screen)
        self.typed = True  # False when a key shortcut / click triggered the command (no echo, no chatter)
        first = self.add_context(KINDS["notes"](topic="scratch"))
        self.screens["main"] = Screen.create("main", first.id)

    # -- state helpers
    screen = property(lambda s: s.screens[s.cur])
    pane = property(lambda s: s.screen.focus)
    ctx = property(lambda s: s.contexts[s.pane.ctx])

    def add_context(self, params: ContextParams) -> Context:
        return self.contexts.setdefault(params.ctx_hash(), Context(params))  # same params -> same ctx

    def resolve(self, prefix: str | None) -> Context:
        if prefix is None:
            return self.ctx
        hits = [c for h, c in self.contexts.items() if h.startswith(prefix)]
        if len(hits) != 1:
            raise CmdError(f"{'no' if not hits else 'ambiguous'} context for '{prefix}'")
        return hits[0]

    def running(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for s in self.screens.values():
            for p in panes(s.root):
                out.setdefault(p.ctx, []).append(f"{s.name}:{p.id}")
        return out

    def out(self, text: str = "") -> None:
        self.pane.buf.extend(text.split("\n"))

    # -- persistence (tmux-resurrect style): contexts by (kind, params), layouts as plain JSON
    def snapshot(self, current_only: bool = False, scrollback: bool = False) -> dict:
        screens = [self.screen] if current_only else list(self.screens.values())

        def enc(n: Node) -> dict:
            if isinstance(n, Pane):
                d = {"t": "pane", "id": n.id, "ctx": n.ctx, "color": n.color, "slot": n.slot, "box": list(n.box) if n.box else None}
                if scrollback:
                    d["buf"] = list(n.buf)
                return d
            return {"t": "split", "dir": n.direction, "ratio": n.ratio, "a": enc(n.a), "b": enc(n.b)}

        used = {p.ctx for s in screens for p in panes(s.root)}
        return {
            "version": 1, "saved_at": time.time(), "current": self.cur,
            "contexts": [{"id": h, "kind": c.params.kind, "params": c.params.model_dump(mode="json"), "state": c.state}
                         for h, c in self.contexts.items() if not current_only or h in used],
            "screens": [{"name": s.name, "focus": s.focus.id, "layout": enc(s.root), "next_slot": s.next_slot,
                         "palette": {"index": s.palette, "colors": s.colors}}
                        for s in screens],
        }

    def apply_snapshot(self, data: dict, wipe: bool = False) -> list[str]:
        """Rebuild from a snapshot. Same-named screens are replaced; others are kept unless `wipe`.
        Builds everything first and only then mutates, so a bad file can't leave the app half-restored."""
        notes: list[str] = []
        idmap: dict[str, str] = {}   # saved id -> id under the *current* models (hash may differ if a model changed)
        ctxs: dict[str, Context] = {}
        for c in data.get("contexts", []):
            try:
                model = KINDS.get(c.get("kind"))
                if model is None:
                    raise ValueError(f"unknown kind '{c.get('kind')}'")
                params = model(**c["params"])
            except (ValueError, TypeError, KeyError) as e:  # pydantic's ValidationError is a ValueError
                notes.append(f"skipped context {str(c.get('id'))[:8]}: {str(e).splitlines()[0]}")
                continue
            idmap[c["id"]] = params.ctx_hash()
            ctxs[params.ctx_hash()] = Context(params, c.get("state") or {})

        def dec(d: dict) -> Node | None:
            if d.get("t") == "pane":
                h = idmap.get(d.get("ctx"))
                if h is None:
                    return None  # its context could not be restored: drop the pane, the tree collapses
                slot = d.get("slot")
                slot = slot if isinstance(slot, int) and 0 <= slot < NSLOTS else None
                box = d.get("box")
                ok = isinstance(box, list) and len(box) == 4 and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in box)
                p = Pane(int(d["id"]), h, color=norm_color(d.get("color")), slot=slot, box=clamp_box(*box) if ok else None)
                p.buf.extend(map(str, d.get("buf", [])))
                return p
            a, b = dec(d["a"]), dec(d["b"])
            if a is None or b is None:
                return b if a is None else a
            return Split("v" if d.get("dir") == "v" else "h", a, b, min(0.9, max(0.1, float(d.get("ratio", 0.5)))))

        screens: dict[str, Screen] = {}
        for s in data.get("screens", []):
            try:
                root = dec(s["layout"])
                if root is None:
                    notes.append(f"skipped screen '{s['name']}': no usable panes")
                    continue
                ps = list(panes(root))
                focus = next((p for p in ps if p.id == s.get("focus")), ps[0])
                name = str(s["name"])
                screens[name] = Screen(name, root, focus, itertools.count(max(p.id for p in ps) + 1),
                                       palette_index_from_json(s.get("palette")), int(s.get("next_slot", len(ps))))
            except (KeyError, TypeError, ValueError, AttributeError) as e:
                notes.append(f"skipped a malformed screen ({e!r})")
        if not screens:
            raise CmdError("snapshot has no usable screens")
        if wipe:
            self.contexts.clear()
            self.screens.clear()
        self.contexts.update(ctxs)
        self.screens.update(screens)
        if data.get("current") in screens:
            self.cur = data["current"]
        elif self.cur not in self.screens:
            self.cur = next(iter(self.screens))
        return notes

    def load_file(self, name: str, wipe: bool = False) -> tuple[Path, list[str]]:
        path = snapshot_path(name)
        try:
            data = json.loads(path.read_text())
        except FileNotFoundError:
            raise CmdError(f"no snapshot '{name}' (try: saves)")
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise CmdError(f"corrupt snapshot '{name}': {e}")
        return path, self.apply_snapshot(data, wipe)

    # -- repl
    def execute(self, line: str, typed: bool = True) -> None:
        self.typed = typed
        try:
            argv = shlex.split(line)
        except ValueError as e:
            return self.out(f"error: {e}")
        if not argv:
            return
        if typed:
            self.out(f"$ {line}")
        table = {**GLOBAL, **self.ctx.params.commands}
        cls = table.get(argv[0])
        if cls is None:
            return self.out(f"unknown command '{argv[0]}' (try: help)")
        try:
            cls.parse(argv[1:]).run(self)
        except ParseExit as e:
            self.out(str(e))
        except ValidationError as e:
            self.out("; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors()))
        except CmdError as e:
            self.out(f"error: {e}")

    def prompt(self) -> str:
        return f"{self.cur}:{self.pane.id} {self.ctx.params.kind}:{self.ctx.id[:6]}> "

    def render(self) -> None:
        size = shutil.get_terminal_size((80, 24))
        W, H = size.columns, max(size.lines - 3, 5)
        grid = [[" "] * W for _ in range(H)]

        def put(x, y, s):
            for i, ch in enumerate(s):
                if 0 <= x + i < W and 0 <= y < H:
                    grid[y][x + i] = ch

        for p, (x, y, w, h) in rects(self.screen.root, 0, 0, W, H):
            if w < 4 or h < 3:
                continue
            foc = p is self.screen.focus
            tl, tr, bl, br, hz, vt = "╔╗╚╝═║" if foc else "┌┐└┘─│"
            put(x, y, tl + hz * (w - 2) + tr)
            put(x, y + h - 1, bl + hz * (w - 2) + br)
            for r in range(1, h - 1):
                put(x, y + r, vt)
                put(x + w - 1, y + r, vt)
            put(x + 1, y, f" {'*' if foc else ''}{p.id} {self.contexts[p.ctx].params.label()} "[: w - 2])
            lines = []
            for l in p.buf:
                lines += [l[i:i + w - 2] for i in range(0, max(len(l), 1), w - 2)]
            for i, l in enumerate(lines[-(h - 2):]):
                put(x + 1, y + 1 + i, l)
        if sys.stdout.isatty():
            print("\033[2J\033[H", end="")
        print("\n".join("".join(r) for r in grid))
        print(" ".join(f"[{n}]{'*' if n == self.cur else ''}" for n in self.screens))


# ───────────────────────────── built-in commands ──────────────────────────────
@command("ls")
class Ls(Command):
    """List all known contexts (* = running in a pane)."""
    def run(self, app):
        run = app.running()
        for h, c in app.contexts.items():
            app.out(f"{'*' if h in run else ' '} {h[:8]}  {c.params.label()}")


@command("ps")
class Ps(Command):
    """List running contexts (shown in some pane) with their screen:pane."""
    def run(self, app):
        for h, locs in app.running().items():
            app.out(f"{h[:8]}  {app.contexts[h].params.label()}  @ {', '.join(locs)}")


@command("screens")
class Screens(Command):
    """List screens (sessions)."""
    def run(self, app):
        for n, s in app.screens.items():
            app.out(f"{'*' if n == app.cur else ' '} {n}  ({len(list(panes(s.root)))} panes)")


@command("screen")
class ScreenCmd(Command):
    """Switch to screen NAME, creating it (with the current context) if needed."""
    name_: str

    @classmethod
    def parser(cls):  # `name` is taken by ClassVar, so expose the field as positional `name`
        p = _Parser(prog=cls.name, description=cls.__doc__)
        p.add_argument("name_", metavar="name")
        return p

    def run(self, app):
        if self.name_ not in app.screens:
            # a new screen keeps the palette of the screen it is created from
            app.screens[self.name_] = Screen.create(self.name_, app.ctx.id, app.screen.palette)
        app.cur = self.name_


@command("split")
class Split_(Command):
    """Split the focused pane. h = side by side, v = stacked (tmux semantics)."""
    direction: Literal["h", "v"]
    ctx: str | None = None

    def run(self, app):
        target = app.resolve(self.ctx)
        app.screen.split(self.direction, target.id)


@command("switch")
class Switch(Command):
    """Show another context in the focused pane (contexts are shared, panes are views)."""
    ctx: str

    def run(self, app):
        target = app.resolve(self.ctx)
        others = [l for l in app.running().get(target.id, [])]
        app.pane.ctx = target.id
        if others:
            app.out(f"note: {target.id[:8]} also shown in {', '.join(others)} (shared state)")


@command("focus")
class Focus(Command):
    """Move focus: no argument = next pane; left|right|up|down = neighbouring pane; --id N = pane N."""
    dir: Literal["left", "right", "up", "down"] | None = Field(
        None, description="direction of the pane to focus", json_schema_extra={"positional": True})
    id: int | None = None

    def run(self, app):
        if self.dir and self.id is not None:
            raise CmdError("give a direction or --id, not both")
        ps = list(panes(app.screen.root))
        if self.dir:
            nb = neighbor(app.screen.root, app.pane, self.dir)
            if nb is None:  # edge of the screen: stay put (silently when it came from a key press)
                if app.typed:
                    app.out(f"no pane {self.dir} of here")
                return
            app.screen.focus = nb
        elif self.id is not None:
            hit = [p for p in ps if p.id == self.id]
            if not hit:
                raise CmdError(f"no pane {self.id}")
            app.screen.focus = hit[0]
        else:
            app.screen.focus = ps[(ps.index(app.pane) + 1) % len(ps)]


@command("close")
class Close(Command):
    """Close the focused pane (the context survives, detached)."""
    def run(self, app):
        if len(list(panes(app.screen.root))) > 1:
            return app.screen.close()
        if len(app.screens) == 1:
            raise CmdError("last pane of last screen; use quit")
        del app.screens[app.cur]
        app.cur = next(iter(app.screens))


@command("new")
class New(Command):
    """Create a context of a given kind:  new <kind> <params...>"""
    params: ContextParams

    @classmethod
    def parser(cls):
        p = _Parser(prog=cls.name, description=cls.__doc__)
        sub = p.add_subparsers(dest="kind", required=True)
        for kind, model in KINDS.items():
            fill_parser(sub.add_parser(kind, help=model.__doc__, description=model.__doc__), model)
        return p

    @classmethod
    def parse(cls, argv):
        ns = _given(cls.parser().parse_args(argv))
        return cls(params=KINDS[ns.pop("kind")](**ns))

    def run(self, app):
        existed = self.params.ctx_hash() in app.contexts
        c = app.add_context(self.params)
        app.out(f"{'exists' if existed else 'created'} {c.id[:8]}  {c.params.label()}")


@command("kill")
class Kill(Command):
    """Delete a context (it must not be displayed in any pane)."""
    ctx: str

    def run(self, app):
        c = app.resolve(self.ctx)
        if c.id in app.running():
            raise CmdError("context is running in a pane; close/switch those panes first")
        del app.contexts[c.id]


@command("color")
class ColorCmd(Command):
    """Pane border colour. No argument = next colour of the screen's palette; prev; 1-5 = that slot;
    #rgb/#rrggbb = custom colour; reset = none."""
    value: str | None = Field(None, description="next | prev | 1-5 | #hex | reset",
                              json_schema_extra={"positional": True})

    def run(self, app):
        p, v = app.pane, (self.value or "next").lower()
        if v in ("next", "prev"):  # deterministic cycling, never random
            step = 1 if v == "next" else -1
            p.slot = (p.slot + step) % NSLOTS if p.slot is not None else (0 if step > 0 else NSLOTS - 1)
            p.color = None
        elif v.isdigit() and 1 <= int(v) <= NSLOTS:
            p.slot, p.color = int(v) - 1, None
        elif v in ("reset", "none", "default"):
            p.slot = p.color = None
        elif (c := norm_color(v)) is not None:
            p.slot, p.color = None, c
        else:
            raise CmdError(f"not a hex colour: {self.value!r} (use next, prev, 1-{NSLOTS}, #rgb, #rrggbb or reset)")
        if app.typed:
            shown = app.screen.color_of(p)
            app.out(f"border colour: {shown or 'default'}" + (f"  (palette {p.slot + 1}/{NSLOTS})" if p.slot is not None else ""))


@command("palette")
class PaletteCmd(Command):
    """Switch this screen's palette (there are 4). No argument = the next one; N = palette N; --show only prints."""
    n: int | None = Field(None, description="palette number", json_schema_extra={"positional": True})
    show: bool = Field(False, description="print the current palette, change nothing")

    def run(self, app):
        scr = app.screen
        if not self.show:
            if self.n is None:
                scr.palette = (scr.palette + 1) % len(PALETTES)
            elif 1 <= self.n <= len(PALETTES):
                scr.palette = self.n - 1
            else:
                raise CmdError(f"palette must be 1-{len(PALETTES)}")
        if app.typed or self.show:
            app.out(f"palette {scr.palette + 1}/{len(PALETTES)}: " + " ".join(f"{i + 1}={c}" for i, c in enumerate(scr.colors)))


@command("save")
class Save(Command):
    """Save contexts + layouts to $CTXMUX_DIR (default ~/.ctxmux)/<file>.json. All screens unless --current."""
    file: str = Field("default", description="snapshot name", json_schema_extra={"positional": True})
    current: bool = Field(False, description="only the current screen (and the contexts it uses)")
    scrollback: bool = Field(False, description="also save each pane's scrollback")

    def run(self, app):
        path = snapshot_path(self.file)
        data = app.snapshot(self.current, self.scrollback)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")  # write + rename: a crash never leaves a half-written snapshot
        tmp.write_text(json.dumps(data, indent=1, default=str))
        tmp.replace(path)
        app.out(f"saved {len(data['screens'])} screen(s), {len(data['contexts'])} context(s) -> {path}")


@command("restore")
class Restore(Command):
    """Load a snapshot. Same-named screens are replaced, other screens stay unless --wipe."""
    file: str = Field("default", description="snapshot name", json_schema_extra={"positional": True})
    wipe: bool = Field(False, description="drop all current screens and contexts first")

    def run(self, app):
        path, notes = app.load_file(self.file, self.wipe)
        app.out(f"restored {path.name}: screens = {', '.join(app.screens)}")
        for n in notes:
            app.out(f"  warning: {n}")


@command("saves")
class Saves(Command):
    """List saved snapshots."""
    def run(self, app):
        d = snapshot_path("x").parent
        files = sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True) if d.exists() else []
        for p in files:
            try:
                data = json.loads(p.read_text())
                info = f"{len(data['screens'])} screens, {len(data['contexts'])} contexts"
            except (ValueError, KeyError, TypeError):
                info = "unreadable"
            app.out(f"{p.stem:14} {time.strftime('%Y-%m-%d %H:%M', time.localtime(p.stat().st_mtime))}  {info}")
        if not files:
            app.out(f"no snapshots in {d}")


@command("help")
class Help(Command):
    """List commands available here (use `<cmd> -h` for details)."""
    def run(self, app):
        for n, c in {**GLOBAL, **app.ctx.params.commands}.items():
            app.out(f"{n:8} {(c.__doc__ or '').strip().splitlines()[0] if c.__doc__ else ''}")


@command("quit")
class Quit(Command):
    """Leave."""
    def run(self, app):
        app.alive = False


# ───────────────────────────── example context kinds ──────────────────────────
@context_type("ssh")
class SSH(ContextParams):
    """Remote server session."""
    host: IPv4Address
    reason: str


@command("run", on=SSH)
class SSHRun(Command):
    """Run a (fake) remote command."""
    cmd: list[str]
    timeout: int = 30

    def run(self, app):
        host = app.ctx.params.host
        app.out(f"[{host}] $ {' '.join(self.cmd)}   (timeout={self.timeout}s)")
        app.ctx.state["runs"] = app.ctx.state.get("runs", 0) + 1
        app.out(f"[{host}] ok (run #{app.ctx.state['runs']})")


@context_type("notes")
class Notes(ContextParams):
    """A scratchpad about a topic."""
    topic: str


@command("add", on=Notes)
class NotesAdd(Command):
    """Append a note (shared by every pane showing this context)."""
    text: list[str]

    def run(self, app):
        app.ctx.state.setdefault("notes", []).append(" ".join(self.text))


@command("show", on=Notes)
class NotesShow(Command):
    """Print notes."""
    def run(self, app):
        for i, n in enumerate(app.ctx.state.get("notes", []), 1):
            app.out(f"{i}. {n}")


def main() -> None:
    app = App()
    if "--resume" in sys.argv:  # restore the "default" snapshot at startup
        try:
            app.load_file("default", wipe=True)
        except CmdError as e:
            print(e)
    while app.alive:
        app.render()
        try:
            line = input(app.prompt())
        except (EOFError, KeyboardInterrupt):
            break
        app.execute(line)


if __name__ == "__main__":
    main()