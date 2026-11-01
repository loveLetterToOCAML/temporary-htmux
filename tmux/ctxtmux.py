#!/usr/bin/env python3
"""ctxmux_htmx: htmx front-end for ctxmux.py. The server renders HTML; htmx just swaps it in.

    python ctxmux_htmx.py                # http://127.0.0.1:8765
    python ctxmux_htmx.py --selftest     # drives the endpoints with httpx

Routes
    GET  /                page (shell + current screen, server-rendered)
    GET  /screen?since=N  the screen fragment, or 204 (htmx swaps nothing) if rev is still N
    POST /exec            form field `line`; runs the command, returns the screen fragment

Windows: shift+click a pane -> `modal <id>` floats it; any number at once, sized from the pane's box (small boxes grow
more than big ones shrink, aspect kept); geometry saved per pane once you move/resize; again -> back in its box.
Every clickable thing (pane, tab, command bar) is just `hx-post="/exec"` with a command line,
so mouse, keyboard and typed commands all go through the same path.
Drop htmx.min.js next to this file to serve it locally instead of from the CDN.
"""
from __future__ import annotations

import argparse
import json
import re
import threading
from contextlib import contextmanager
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from pydantic import Field

from ctxtmux_base import PALETTES, App, CmdError, Command, Pane, Split, clamp_box, command, panes, visible

app = App()
lock = threading.Lock()
rev = 0
clients: dict[str, dict] = {}  # browser-tab id -> view state: current screen + its windows (modals) per screen
LOCAL_HTMX = Path(__file__).with_name("htmx.min.js")
HTMX_SRC = "/htmx.min.js" if LOCAL_HTMX.exists() else "https://cdn.jsdelivr.net/npm/htmx.org@2.0.4/dist/htmx.min.js"


@contextmanager
def as_client(cid: str):
    """Run with this browser tab's view state loaded into `app` (current screen, windows), mutated in place and kept.
    Screens, panes and contexts stay shared; only what *this tab is looking at* is per tab."""
    st = clients.setdefault(cid, {"cur": "main", "wins": {}, "mins": {}})
    app.cur = st["cur"] if st["cur"] in app.screens else next(iter(app.screens))
    app.wins, app.mins = st["wins"], st["mins"]
    try:
        yield
    finally:
        st["cur"] = app.cur


def _pane(pid: int | None) -> Pane | None:
    return next((p for p in panes(app.screen.root) if p.id == pid), None)


@command("modal")
class Modal(Command):
    """Show a pane in a window (shift+click a pane). Any number can be open; the same command closes it again."""
    id: int | None = Field(None, description="pane id (default: the focused pane)", json_schema_extra={"positional": True})
    mini: bool = Field(False, description="reduce / expand this pane's window to its title bar")
    front: bool = Field(False, description="bring this pane's window to the front and focus the pane")
    all: bool = Field(False, description="close every window of this screen")

    def run(self, app):
        wins = app.modals
        if self.all:
            wins.clear()
            app.minis.clear()
            return
        pid = self.id if self.id is not None else app.pane.id
        pane = _pane(pid)
        if pane is None:
            raise CmdError(f"no pane {pid}")
        if self.mini or self.front:
            if pid not in wins:
                raise CmdError(f"pane {pid} has no window")
            if self.mini:
                app.minis.symmetric_difference_update({pid})
            else:
                wins.remove(pid)
                wins.append(pid)
        elif pid in wins:  # toggle: close
            wins.remove(pid)
            app.minis.discard(pid)
        else:
            wins.append(pid)  # open on top
        app.screen.focus = pane  # you type into the pane whose window you just used


@command("modalbox")
class ModalBox(Command):
    """Remember where a pane's window is and how big (the page sends this after you move or resize it)."""
    id: int
    x: int
    y: int
    w: int
    h: int

    def run(self, app):
        pane = _pane(self.id)
        if pane is None:
            raise CmdError(f"no pane {self.id}")
        pane.box = clamp_box(self.x, self.y, self.w, self.h)
        if self.id in app.modals:  # touching a window raises it, like any window system
            app.modals.remove(self.id)
            app.modals.append(self.id)
        app.screen.focus = pane


def resize(path: str, ratio: str) -> None:
    """Set the ratio of the split at `path` (relative to this client's screen root)."""
    n = app.screen.root
    for step in path:
        if not isinstance(n, Split) or step not in "ab":
            return  # layout changed under us (another tab); ignore
        n = n.a if step == "a" else n.b
    if isinstance(n, Split):
        try:
            n.ratio = min(0.9, max(0.1, float(ratio)))
        except ValueError:
            pass


# ───────────────────────────── server-side rendering ──────────────────────────
def post_attrs(line: str, quiet: bool = False, trigger: str = "") -> str:
    """htmx attributes making an element run `line` on click (quiet: don't echo it into the pane)."""
    vals = {"line": line, **({"quiet": "1"} if quiet else {})}
    trig = f' hx-trigger="{escape(trigger, quote=True)}"' if trigger else ""
    return f'hx-post="/exec"{trig} hx-vals=\'{escape(json.dumps(vals), quote=True)}\''


MIN_W, MIN_H, BAR, GAP = 140, 64, 5, 4  # px: min pane size, bar thickness, pane margins (2px per side)


def min_size(n) -> tuple[int, int]:
    """Smallest outer (w, h) a node needs so that no pane inside drops below MIN_W x MIN_H."""
    if isinstance(n, Pane):
        return MIN_W + GAP, MIN_H + GAP
    (aw, ah), (bw, bh) = min_size(n.a), min_size(n.b)
    return (aw + BAR + bw, max(ah, bh)) if n.direction == "h" else (max(aw, bw), ah + BAR + bh)


def tree(n, scr, path: str = "", grow: float = 1.0) -> str:
    """`path` addresses a split from the root ('' = root, 'a' = its first child, ...); `grow` is its flex share."""
    if not isinstance(n, Pane):
        w, h = min_size(n)
        return (f'<div class="split {n.direction}" style="flex-grow:{grow:.4f};min-width:{w}px;min-height:{h}px">'
                f'{tree(n.a, scr, path + "a", n.ratio)}'
                f'<div class="bar" data-path="{path}" title="drag to resize, double-click to reset"></div>'
                f'{tree(n.b, scr, path + "b", 1 - n.ratio)}</div>')
    c = app.contexts[n.ctx]
    focus = n is scr.focus
    attrs = "" if focus else post_attrs(f"focus --id {n.id}", quiet=True, trigger="click[!shiftKey]")  # clicking an unfocused pane focuses it
    col = scr.color_of(n)  # custom colour, else the screen's palette slot
    col = visible(col) if col else col  # too-dark palette colours (black, navy) are lifted so they show on the dark UI
    color = f";--pc:{col};--pcd:{col}b3" if col else ""  # validated #rrggbb; --pcd = 70% alpha
    in_modal = n.id in app.modals
    cls = "pane" + (" focus" if focus else "") + (" in-modal" if in_modal else "")
    body = ('<div class="ph">⧉ shown in a window — shift+click to bring it back</div>' if in_modal
            else f'<pre>{escape(chr(10).join(n.buf))}</pre>')
    return (f'<div class="{cls}" data-id="{n.id}" '
            f'style="flex-grow:{grow:.4f};min-width:{MIN_W}px;min-height:{MIN_H}px{color}" {attrs}>'
            f'<div class="head">{n.id} {escape(c.params.label())} <i>{c.id[:8]}</i></div>{body}</div>')


def accent_style() -> str:
    """The focused pane's colour becomes the UI accent (prompt, current tab, hint, bar hover) via a CSS variable.
    Set inline on the elements that use it: robust, and no stylesheet swapping needed."""
    col = app.screen.color_of(app.pane)
    col = visible(col) if col else col
    return f' style="--accent:{col}"' if col else ""


def prompt_span(oob: bool) -> str:
    return f'<span id="prompt"{" hx-swap-oob=true" if oob else ""}{accent_style()}>{escape(app.prompt())}</span>'


def modals_html() -> str:
    """Floating windows, one per pane in `app.modals` (bottom -> top). Each shows its pane's live content while the pane
    stays in the layout, greyed. A window with no saved geometry is marked data-auto: the page sizes it from the pane's
    current box (see autoBox in the script) and reveals it. Saved geometry is plain inline px."""
    ids = {p.id for p in panes(app.screen.root)}
    app.modals[:] = [i for i in app.modals if i in ids]  # panes closed since: drop their windows
    app.minis.intersection_update(ids)
    out = []
    for z, pid in enumerate(app.modals):
        pane = _pane(pid)
        c, col = app.contexts[pane.ctx], app.screen.color_of(pane)
        style = f"z-index:{50 + z};"
        if pane.box:
            x, y, bw, bh = pane.box  # keep at least the title bar reachable if the browser window got smaller
            style += f"left:min({x}px,calc(100vw - 80px));top:min({y}px,calc(100vh - 40px));width:{bw}px;height:{bh}px;"
        if col:
            style += f"--pc:{visible(col)};"
        mini = pid in app.minis
        # No id on purpose: htmx "settles" style/class of same-id elements (old value first, new one 20 ms later), which
        # would wipe the geometry the page script applies right after the swap.
        attrs = (f' class="modal{" mini" if mini else ""}" data-pane="{pid}" data-top="{int(z == len(app.modals) - 1)}"'
                 f' data-focus="{int(app.pane is pane)}"' + ("" if pane.box else " data-auto"))
        out.append(
            f'<div{attrs} style="{style}">'
            f'<div class="mhead"><span>{pid} {escape(c.params.label())} <i>{c.id[:8]}</i></span>'
            f'<button {post_attrs(f"modal {pid} --mini", quiet=True)} title="{"expand" if mini else "reduce"}">{"▢" if mini else "–"}</button>'
            f'<button {post_attrs(f"modal {pid}", quiet=True)} title="back into its box">×</button></div>'
            f'<pre>{escape(chr(10).join(pane.buf))}</pre><div class="grip"></div></div>')
    return "".join(out)


def screen_html(oob_prompt: bool = True) -> str:
    tabs = "".join(
        f'<button class="{"cur" if n == app.cur else ""}" {post_attrs(f"screen {n}", quiet=True)}>{escape(n)}</button>'
        for n in app.screens)
    colors, p = app.screen.colors, app.pane
    chips = "".join(
        f'<button class="chip{" on" if p.color is None and p.slot == i else ""}" style="background:{col}" '
        f'title="colour {i + 1}" {post_attrs(f"color {i + 1}", quiet=True)}></button>'
        for i, col in enumerate(colors))
    out = (f'<div id="screen"{accent_style()} hx-get="/screen?since={rev}" hx-trigger="every 2s" '
           f'hx-target="#screen" hx-swap="outerHTML">'
           f'<div id="root">{tree(app.screen.root, app.screen)}</div>'
           f'<div id="tabs">{tabs}<span id="chips" title="palette {app.screen.palette + 1}/{len(PALETTES)} — ctrl+b p for the next one">{chips}</span>'
           f'<span id="hint">ctrl+b: % split-h &nbsp; " split-v &nbsp; x close &nbsp; '
           f'o next &nbsp; ←↑↓→ pane &nbsp; c colour &nbsp; p palette &nbsp; shift+click: window (several ok)</span></div>{modals_html()}</div>')
    return out + (prompt_span(True) if oob_prompt else "")


# ───────────────────────────────── http layer ─────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def _send(self, body: bytes = b"", ctype="text/html; charset=utf-8", code=200):
        self.send_response(code)
        if body:
            self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        cid = self.headers.get("X-Client", "")
        if u.path == "/":
            return self._send(PAGE.encode())
        if u.path == "/screen":
            since = int(parse_qs(u.query).get("since", ["-1"])[0])
            with lock, as_client(cid):
                if since == rev:
                    return self._send(code=204)  # nothing changed: htmx swaps nothing
                return self._send(screen_html().encode())
        if u.path == "/htmx.min.js" and LOCAL_HTMX.exists():
            return self._send(LOCAL_HTMX.read_bytes(), "text/javascript")
        self._send(b"not found", "text/plain", 404)

    def do_POST(self):
        global rev
        if self.path not in ("/exec", "/resize"):
            return self._send(b"not found", "text/plain", 404)
        cid = self.headers.get("X-Client", "")
        form = parse_qs(self.rfile.read(int(self.headers.get("Content-Length", 0))).decode())
        if self.path == "/resize":
            with lock, as_client(cid):
                resize(form.get("path", [""])[0], form.get("ratio", ["0.5"])[0])
                rev += 1
                return self._send(screen_html().encode())
        line = form.get("line", [""])[0]
        with lock, as_client(cid):
            app.execute(line, typed=not form.get("quiet", [""])[0])
            if not app.alive:  # `quit` is meaningless for a shared web session
                app.alive = True
                app.out("quit: close the tab instead")
            rev += 1
            self._send(screen_html().encode())

    def log_message(self, *a):
        pass


def serve(port: int = 8765) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def selftest() -> None:
    import httpx

    import os, tempfile
    os.environ["CTXMUX_DIR"] = tempfile.mkdtemp()  # keep tests away from ~/.ctxmux
    srv = serve(0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    with httpx.Client(base_url=base) as c:
        A, B = {"X-Client": "tabA"}, {"X-Client": "tabB"}
        cur = lambda html: html.split('class="cur"')[1].split(">")[1].split("<")[0]
        assert "hx-trigger=\"load\"" in c.get("/").text
        for line in ["new ssh 10.0.0.1 deploy", "split h", "switch c4", "run uptime", "split v --ctx c4"]:
            frag = c.post("/exec", data={"line": line}, headers=A).text
        print("panes:", frag.count('class="pane'), "| focused:", frag.count("pane focus"))
        print("oob prompt present:", 'hx-swap-oob=true' in frag)
        a = c.post("/exec", data={"line": "screen ops"}, headers=A).text
        b = c.get("/screen", params={"since": -1}, headers=B).text
        print("tab A on:", cur(a), "| tab B on:", cur(b))
        assert cur(a) == "ops" and cur(b) == "main"
        print("A still on:", cur(c.get("/screen", params={"since": -1}, headers=A).text))
        # resize on `main` (root split h: pane 1 | split v of 2 panes): paths "" = root, "b" = the inner split
        c.post("/exec", data={"line": "screen main"}, headers=A)
        grows = lambda html: re.findall(r"flex-grow:([0-9.]+)", html)
        print("initial grows:", grows(c.post("/resize", data={"path": "zz", "ratio": "0.2"}, headers=A).text), "(bogus path ignored)")
        print("root -> 0.7:  ", grows(c.post("/resize", data={"path": "", "ratio": "0.7"}, headers=A).text))
        print("inner -> 5 (clamped to 0.9):", grows(c.post("/resize", data={"path": "b", "ratio": "5"}, headers=A).text))
        after = c.post("/exec", data={"line": "split h"}, headers=A).text
        print("after another split, root still 0.7:", grows(after)[:3])
        print("tab B sees the shared layout:", grows(c.get("/screen", params={"since": -1}, headers=B).text)[:3])
        # min sizes (server-computed) on the root split: pane | (pane / pane) -> nested minimums add up
        root = re.search(r'class="split h" style="([^"]*)"', after).group(1)
        print("root minimums:", re.findall(r"min-(?:width|height):\d+px", root))
        # colours: explicit hex, shorthand, random, bad input
        col = lambda html: re.findall(r"--pc:(#[0-9a-f]{6})", html)
        print("color #fa0     ->", col(c.post("/exec", data={"line": "color #fa0"}, headers=A).text))
        r1 = col(c.post("/exec", data={"line": "color"}, headers=A).text)
        print("color (random) ->", r1)
        print("bad color      ->", "not a hex colour" in c.post("/exec", data={"line": "color zzz"}, headers=A).text)
        # persistence: save, wreck the state, restore --wipe, compare the shape (ratios + colours)
        shape = lambda html: re.findall(r"flex-grow:[0-9.]+|--pc:#[0-9a-f]{6}", html)
        before = shape(c.get("/screen", params={"since": -1}, headers=A).text)
        print(c.post("/exec", data={"line": "save"}, headers=A).text.split("saved")[1].split("<")[0].strip()[:40])
        c.post("/exec", data={"line": "close"}, headers=A)
        c.post("/exec", data={"line": "screen tmp"}, headers=A)
        c.post("/exec", data={"line": "color reset"}, headers=A)
        back = c.post("/exec", data={"line": "restore --wipe"}, headers=A).text
        print("roundtrip identical:", shape(back) == before, "| screens:", re.findall(r"class=\"[^\"]*\" hx-post=\"/exec\" hx-vals='[^']*'>(\w+)</button>", back))
        print("saved focus survives:", back.count("pane focus"))
        print("saves ->", c.post("/exec", data={"line": "saves"}, headers=A).text.count("default"), "default entry")
        # directional focus on a fresh screen:  1 | (2 over 3), focus starts on 3
        C = {"X-Client": "tabC"}
        for line in ["screen nav", "split h", "split v"]:
            c.post("/exec", data={"line": line}, headers=C)
        foc = lambda html: re.search(r'class="pane focus"[^>]*><div class="head">(\d+) ', html).group(1)
        go = lambda d, **kw: c.post("/exec", data={"line": f"focus {d}", **kw}, headers=C).text
        walk = [f"{d}->{foc(go(d))}" for d in ["left", "right", "up", "down", "up", "up"]]
        print("focus walk from pane 3:", " ".join(walk))
        typed = go("up")
        quiet = go("up", quiet="1")
        print("typed edge says so:", "no pane up of here" in typed, "| quiet edge silent:",
              quiet.count("no pane up") == typed.count("no pane up"), "| quiet leaves no echo:",
              quiet.count("$ focus up") == typed.count("$ focus up"))
        # regression: optional positionals (with and without choices/types) when omitted, `--id=3` form
        ok = lambda line: not re.search(r"error:|Traceback", c.post("/exec", data={"line": line, "quiet": "1"}, headers=C).text.split("$ ")[-1])
        cases = ["focus", "focus --id=1", "focus --id 2", "focus left", "color", "color #abc", "color reset", "save", "save x", "restore x"]
        print("optional-positional cases:", {k: ok(k) for k in cases if not ok(k)} or "all ok")
        # accent follows the focused pane's colour: split -> new (uncoloured) pane focused -> accent gone; focus left -> back
        D = {"X-Client": "tabD"}
        post = lambda line: c.post("/exec", data={"line": line}, headers=D).text
        post("screen acc")
        acc = lambda html: html.count('style="--accent:#00ff00"')
        print("accent on colored pane (screen + prompt):", acc(post("color #0f0")),
              "| after split (uncolored focus):", acc(post("split h")),
              "| focus left:", acc(post("focus left")))
        # palettes: exactly 4, per screen; panes take successive slots; `color` cycles; new screens inherit; ctrl+b p = next
        import ctxmux
        P, V = ctxmux.PALETTES, visible  # borders show the exact colour, except too-dark ones, which are lifted
        E = {"X-Client": "tabE"}
        px = lambda line, **kw: c.post("/exec", data={"line": line, **kw}, headers=E).text
        chips = lambda h: re.findall(r'class="chip[^"]*" style="background:(#[0-9a-f]{6})"', h)
        focus_pc = lambda h: re.search(r'class="pane focus[^"]*" data-id="\d+" style="[^"]*--pc:(#[0-9a-f]{6})', h).group(1)
        print("exactly 4 palettes of 5:", len(P) == 4 and all(len(p) == 5 for p in P))
        h = px("screen pal"); p0 = chips(h)
        print("first screen starts on palette 1:", p0 == P[0], "| pane 1 = slot 1:", focus_pc(h) == V(p0[0]))
        h = px("split h"); print("new pane takes slot 2:", focus_pc(h) == V(p0[1]))
        seen = [focus_pc(px("color")) for _ in range(5)]
        print("color cycles in order, wraps:", seen == [V(x) for x in (p0[2], p0[3], p0[4], p0[0], p0[1])])
        walk = [chips(px("palette")) for _ in range(5)]
        print("`palette` walks 1->2->3->4->1 (5th call wraps):", walk == [P[1], P[2], P[3], P[0], P[1]])
        h = px("palette"); print("panes recoloured with the palette (same slot):", chips(h) == P[2] and focus_pc(h) == V(P[2][1]))
        h = px("palette 4"); print("palette 4:", chips(h) == P[3], "| out of range rejected:", "palette must be 1-4" in px("palette 9"), "| still 4:", chips(px("palette --show")) == P[3])
        h = px("screen other"); print("new screen keeps the same palette:", chips(h) == P[3])
        px("palette 1"); h = px("screen pal")
        print("screens are independent (pal still 4, other now 1):", chips(h) == P[3], chips(px("screen other")) == P[0])
        h = px("color 4"); print("color 4 / chip click:", focus_pc(h) == V(chips(h)[3]))
        print("custom hex overrides, `color` goes back to palette:", focus_pc(px("color #123456")) == V("#123456"), focus_pc(px("color")) in [V(x) for x in chips(h)])
        px("screen pal"); px("palette 3"); px("save pt")
        px("palette 1"); back = px("restore pt --wipe")
        print("palette survives save/restore:", chips(px("screen pal")) == P[2])
        lum = lambda x: (lambda r, g, b: .2126 * r + .7152 * g + .0722 * b)(*[(lambda v: v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4)(int(x[i:i + 2], 16) / 255) for i in (1, 3, 5)])
        worst = min((lum(V(x)) + .05) / (lum("#14161a") + .05) for p in P for x in p)
        print("worst drawn contrast over the 20 colours: %.1f" % worst)
        # ---- windows (modals): several at once, per pane, per tab, per screen
        F, G = {"X-Client": "tabF"}, {"X-Client": "tabG"}
        pf = lambda line, **kw: c.post("/exec", data={"line": line, **kw}, headers=F).text
        pg = lambda line, **kw: c.post("/exec", data={"line": line, **kw}, headers=G).text
        wins = lambda h: re.findall(r'<div class="modal[^"]*" data-pane="(\d+)"', h)                       # window order = z-order, bottom -> top
        zs = lambda h: [int(z) for z in re.findall(r'<div class="modal[^"]*" data-pane="\d+"[^>]*style="z-index:(\d+)', h)]
        top = lambda h: re.search(r'<div class="modal[^"]*" data-pane="(\d+)" data-top="1"', h).group(1)
        win = lambda h, i: re.search(rf'<div class="modal[^"]*" data-pane="{i}"[^>]*>', h).group(0)
        greyed = lambda h: re.findall(r'class="pane[^"]*in-modal[^"]*" data-id="(\d+)"', h)
        pf("screen win"); pf("ps"); pf("split h"); pf("split h")          # panes 1,2,3; pane 1 has output; 3 focused
        h = pf("modal 1", quiet="1")
        h = pf("modal 2", quiet="1")
        print("two windows at once:", wins(h) == ["1", "2"], "| both panes greyed:", sorted(greyed(h)) == ["1", "2"], "| z-order 1<2:", zs(h) == [50, 51], "| top is 2:", top(h) == "2")
        print("window shows its pane's live content:", "$ ps" in h.split('data-pane="1"')[1].split("</pre>")[0])
        print("unsaved windows are marked for proportional sizing:", "data-auto" in win(h, 1) and "data-auto" in win(h, 2),
              "| no ids on windows:", 'id="modal' not in h)
        h = pf("modal 1 --front", quiet="1")
        print("--front raises + focuses:", wins(h) == ["2", "1"] and top(h) == "1" and 'class="pane focus in-modal" data-id="1"' in h)
        h = pf("modal 1", quiet="1")
        print("toggle closes only that one:", wins(h) == ["2"] and greyed(h) == ["2"])
        h = pf("modal 1", quiet="1"); h = pf("modal 2 --mini", quiet="1")
        print("reduce is per window:", 'class="modal mini"' in win(h, 2) and 'class="modal"' in win(h, 1))
        h = pf("modalbox 2 100 50 640 400", quiet="1")
        print("saved geometry applied, no longer auto:", "left:min(100px" in win(h, 2) and "width:640px;height:400px" in win(h, 2) and "data-auto" not in win(h, 2),
              "| other window still auto:", "data-auto" in win(h, 1), "| touching raised it:", top(h) == "2")
        h = pf("modalbox 1 -5 3 10 10", quiet="1")
        print("clamped (>=0, min 260x140):", "left:min(0px" in win(h, 1) and "width:260px;height:140px" in win(h, 1))
        pf("modal --all"); h = pf("modal 2", quiet="1")
        print("`modal --all` closes everything; geometry is remembered per pane:", "width:640px;height:400px" in win(h, 2))
        print("per tab: tab G (same screen) has none:", wins(pg("screen win")) == [], "| F keeps its window:", wins(pf("ps")) == ["2"])
        h = pf("screen main"); print("windows are kept per screen: hidden elsewhere:", wins(h) == [], end=" ")
        print("| back again:", wins(pf("screen win")) == ["2"])
        print("errors:", "no pane 99" in pf("modal 99"), "| --mini without window:", "has no window" in pf("modal 3 --mini"), "| --front without window:", "has no window" in pf("modal 3 --front"))
        pf("modal 3", quiet="1"); pf("save wintest"); h = pf("restore wintest --wipe")
        print("geometry survives save/restore:", "left:min(100px" in win(h, 2) and sorted(wins(h)) == ["2", "3"])
        pf("focus --id 3"); pf("close")
        print("closing a pane drops only its window:", wins(pf("ps")) == ["2"])
        print("poll unchanged ->", c.get("/screen", params={"since": rev}, headers=A).status_code)
        print("<script> escaped:", "<script>" not in c.post("/exec", data={"line": "<script>x"}, headers=B).text)
    srv.shutdown()


PAGE = r"""<!doctype html>
<meta charset="utf-8">
<title>ctxmux</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<script src="__HTMX__"></script>
<style>
  :root { --bg:#14161a; --fg:#d7dae0; --dim:#8e95a5; --line:#3d4451; --edge:#5d6677; --accent:#6cb6ff; }
  * { box-sizing: border-box; }
  html, body { height: 100%; margin: 0; background: var(--bg); color: var(--fg);
               font: 13px/1.4 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
  body { height: 100dvh; display: grid; grid-template-rows: 1fr auto; }  /* dvh: mobile URL bars */
  #screen { display: flex; flex-direction: column; min-height: 0; }
  #root { flex: 1; display: flex; min-height: 0; padding: 4px; overflow: auto; }  /* scrolls if the minimums don't fit */
  .split { display: flex; flex: 1 1 0; }  /* min sizes come inline from the server */
  .split.h { flex-direction: row; } .split.v { flex-direction: column; }
  .pane { margin: 2px; flex: 1 1 0; display: flex; flex-direction: column;
          border: 1px solid var(--line); border-radius: 4px; }
  .bar { flex: 0 0 5px; background: var(--line); border-radius: 3px; touch-action: none; transition: background .1s; }
  .bar:hover, .bar.active { background: var(--accent); }
  .split.h > .bar { cursor: col-resize; } .split.v > .bar { cursor: row-resize; }
  body.dragging { user-select: none; }
  .pane[hx-post] { cursor: pointer; }
  .pane { border-color: var(--pcd, var(--edge)); }
  .pane.focus { border-color: var(--pc, var(--accent)); }
  .head { padding: 2px 8px; color: var(--dim); border-bottom: 1px solid var(--line); white-space: nowrap;
          overflow: hidden; text-overflow: ellipsis; }
  .head { color: var(--pcd, var(--dim)); }
  .pane.focus .head { color: var(--pc, var(--accent)); } .head i { opacity: .6; font-style: normal; }
  pre { margin: 0; padding: 6px 8px; flex: 1; overflow: auto; white-space: pre-wrap; word-break: break-word; }
  #tabs { display: flex; gap: 2px; padding: 0 4px; border-top: 1px solid var(--line); }
  #tabs button { background: none; border: 0; color: var(--dim); font: inherit; padding: 3px 10px; cursor: pointer; }
  #tabs button.cur { color: var(--bg); background: var(--accent); transition: background-color .15s; }
  .pane.in-modal { filter: grayscale(1); opacity: .45; }
  .ph { flex: 1; display: flex; align-items: center; justify-content: center; text-align: center; padding: 8px; color: var(--dim); }
  .modal { position: fixed; z-index: 50; left: max(10vw, calc(50vw - 450px)); top: max(15vh, calc(50vh - 260px));
           width: min(900px, 80vw); height: min(520px, 70vh); max-width: calc(100vw - 8px); max-height: calc(100vh - 8px);
           display: flex; flex-direction: column; background: var(--bg); border: 2px solid var(--pc, var(--accent));
           border-radius: 8px; box-shadow: 0 14px 44px rgba(0,0,0,.65); }
  .modal[data-auto] { visibility: hidden; }  /* until the script has sized it from its pane */
  .modal.mini { height: auto !important; } .modal.mini pre, .modal.mini .grip { display: none; }
  .mhead { display: flex; align-items: center; gap: 4px; padding: 4px 6px 4px 10px; cursor: move; user-select: none;
           border-bottom: 1px solid var(--line); color: var(--pc, var(--accent)); }
  .modal.mini .mhead { border-bottom: 0; }
  .mhead span { flex: 1; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; } .mhead i { opacity: .6; font-style: normal; }
  .mhead button { background: none; border: 0; color: var(--fg); font: inherit; cursor: pointer; padding: 0 8px; border-radius: 4px; }
  .mhead button:hover { background: var(--line); }
  .grip { position: absolute; right: 3px; bottom: 3px; width: 14px; height: 14px; cursor: nwse-resize; touch-action: none;
          background: linear-gradient(135deg, transparent 50%, var(--edge) 50%, var(--edge) 60%, transparent 60%, transparent 75%, var(--edge) 75%, var(--edge) 85%, transparent 85%); }
  #chips { display: flex; align-items: center; margin-left: 12px; }
  .chip { box-shadow: inset 0 0 0 1px rgba(255,255,255,.28); width: 13px; height: 13px; border-radius: 50%; border: 2px solid transparent; margin: 0 3px; padding: 0; cursor: pointer; }
  .chip.on { border-color: var(--fg); }
  #hint { margin-left: auto; color: var(--dim); padding: 3px 8px; }
  #bar { display: flex; border-top: 1px solid var(--line); margin: 0; }
  #prompt { padding: 6px 0 6px 8px; color: var(--accent); white-space: pre; transition: color .15s; }
  #cmd { flex: 1; background: none; border: 0; outline: 0; color: var(--fg); font: inherit; padding: 6px 8px; }
</style>
<body hx-headers='{}'>
<div id="screen" hx-get="/screen" hx-trigger="load" hx-target="#screen" hx-swap="outerHTML"></div>
<form id="bar" hx-post="/exec" hx-target="#screen" hx-swap="outerHTML" hx-on::after-request="this.reset()">
  <span id="prompt"></span><input id="cmd" name="line" autocomplete="off" spellcheck="false" autofocus>
</form>
<script>
// htmx does the transport; this is only cosmetics: scroll panes to the bottom, keep focus in the bar.
// per-browser-tab client id: every request carries it, so each tab has its own current screen
const cid = sessionStorage.cid = sessionStorage.cid || Math.random().toString(36).slice(2);
document.body.setAttribute('hx-headers', JSON.stringify({'X-Client': cid}));
const cmd = document.getElementById('cmd');
const settle = () => {
  placeModals();
  document.querySelectorAll('#screen pre').forEach(p => p.scrollTop = p.scrollHeight);
  if (document.activeElement === document.body) cmd.focus();
};
document.body.addEventListener('htmx:afterSwap', settle); settle();

// shell history on arrow up/down
const hist = []; let hi = 0;
cmd.addEventListener('keydown', ev => {
  if (ev.key === 'Enter' && cmd.value.trim()) { hist.push(cmd.value); hi = hist.length; }
  else if (ev.key === 'ArrowUp' && hi > 0) { cmd.value = hist[--hi]; ev.preventDefault(); }
  else if (ev.key === 'ArrowDown') { cmd.value = hist[++hi] || ''; hi = Math.min(hi, hist.length); ev.preventDefault(); }
});

// drag the bars between panes. While dragging, resize locally (no requests); on release POST the ratio.
let drag = null, mdrag = null;
document.body.addEventListener('htmx:beforeRequest', ev => { if (drag || mdrag) ev.preventDefault(); });  // no poll swap mid-drag
const sendResize = (path, ratio) =>
  htmx.ajax('POST', '/resize', {target: '#screen', swap: 'outerHTML', values: {path, ratio}, headers: {'X-Client': cid}});
const ratioAt = (ev, {bar, box}) => {
  const r = box.getBoundingClientRect(), b = bar.getBoundingClientRect(), h = box.classList.contains('h');
  const size = (h ? r.width : r.height) - (h ? b.width : b.height);       // space shared by the two children
  const pos = (h ? ev.clientX - r.left : ev.clientY - r.top) - (h ? b.width : b.height) / 2;
  return Math.min(0.9, Math.max(0.1, pos / size));
};
document.addEventListener('pointerdown', ev => {
  const bar = ev.target.closest && ev.target.closest('.bar');
  if (!bar) return;
  ev.preventDefault(); drag = {bar, box: bar.parentElement, ratio: undefined};
  bar.setPointerCapture(ev.pointerId); bar.classList.add('active'); document.body.classList.add('dragging');
});
document.addEventListener('pointermove', ev => {
  if (!drag) return;
  const r = drag.ratio = ratioAt(ev, drag);
  drag.bar.previousElementSibling.style.flexGrow = r; drag.bar.nextElementSibling.style.flexGrow = 1 - r;
});
const endDrag = send => {
  if (!drag) return;
  const {bar, ratio} = drag; drag = null;
  bar.classList.remove('active'); document.body.classList.remove('dragging');
  if (send && ratio !== undefined) sendResize(bar.dataset.path, ratio);
};
document.addEventListener('pointerup', () => endDrag(true));
document.addEventListener('pointercancel', () => endDrag(false));
document.addEventListener('dblclick', ev => {
  const bar = ev.target.closest && ev.target.closest('.bar'); if (bar) sendResize(bar.dataset.path, 0.5);
});

// Windows. Unsaved ones are sized from their pane's current box: window "size" d' = D0 * (d/D0)^e with d = sqrt(w*h),
// aspect ratio kept. e < 1 compresses: boxes below D0 grow (e=.5, fast), boxes above shrink (e=.8, gently), at D0 nothing
// changes. Then capped to the viewport / 1100x780 and floored at 260x140 (that floor is the only thing that bends the ratio).
function autoBox(r) {
  const D0 = 420, GROW = 0.5, SHRINK = 0.8, d = Math.sqrt(r.width * r.height);
  const k0 = (D0 * Math.pow(d / D0, d < D0 ? GROW : SHRINK)) / d;
  let w = r.width * k0, h = r.height * k0;
  const maxW = Math.min(1100, innerWidth * 0.92), maxH = Math.min(780, innerHeight * 0.9), k = Math.min(1, maxW / w, maxH / h);
  w = Math.min(maxW, Math.max(260, w * k)); h = Math.min(maxH, Math.max(140, h * k));
  // open slightly offset from the box, so its top-left strip (the greyed pane) stays clickable; keep it on screen
  const x = Math.max(8, Math.min(r.left + 24, innerWidth - w - 8)), y = Math.max(8, Math.min(r.top + 24, innerHeight - h - 8));
  return {x: Math.round(x), y: Math.round(y), w: Math.round(w), h: Math.round(h)};
}
function placeModals() {
  document.querySelectorAll('.modal[data-auto]').forEach(m => {
    const p = document.querySelector('.pane[data-id="' + m.dataset.pane + '"]');
    if (!p) return;
    const b = autoBox(p.getBoundingClientRect());
    Object.assign(m.style, {left: b.x + 'px', top: b.y + 'px', width: b.w + 'px', height: b.h + 'px'});
    m.removeAttribute('data-auto');
  });
}
// Move by the title bar, resize with the corner grip; on release the geometry is saved per pane (and raises the window).
// A plain click on a window that is not on top / whose pane is not focused raises + focuses it. Raising is local first
// (z-index), so nothing is swapped while you are dragging.
let zTop = 1000;
document.addEventListener('pointerdown', ev => {
  const m = ev.target.closest && ev.target.closest('.modal');
  if (!m || ev.target.closest('button')) return;
  m.style.zIndex = ++zTop;
  const grip = ev.target.closest('.grip'), head = ev.target.closest('.mhead');
  mdrag = {m, mode: grip ? 'size' : head ? 'move' : 'click', ox: ev.clientX, oy: ev.clientY, r: m.getBoundingClientRect()};
  if (mdrag.mode !== 'click') { (grip || head).setPointerCapture(ev.pointerId); ev.preventDefault(); }  // 'click': let text be selected
});
document.addEventListener('pointermove', ev => {
  if (!mdrag || mdrag.mode === 'click') return;
  const {m, mode, ox, oy, r} = mdrag, dx = ev.clientX - ox, dy = ev.clientY - oy;
  if (mode === 'size') { m.style.width = Math.max(260, r.width + dx) + 'px'; m.style.height = Math.max(140, r.height + dy) + 'px'; }
  else { m.style.left = Math.max(0, r.left + dx) + 'px'; m.style.top = Math.max(0, r.top + dy) + 'px'; }
});
document.addEventListener('pointerup', () => {
  if (!mdrag) return;
  const {m, r} = mdrag, n = m.getBoundingClientRect(), id = m.dataset.pane; mdrag = null;
  if ([n.left - r.left, n.top - r.top, n.width - r.width, n.height - r.height].some(v => Math.abs(v) > 2))
    run(`modalbox ${id} ${Math.round(n.left)} ${Math.round(n.top)} ${Math.round(n.width)} ${Math.round(n.height)}`);
  else if (m.dataset.top !== '1' || m.dataset.focus !== '1') run(`modal ${id} --front`);
});

// shift+click a pane (also a greyed one) -> window / back. Plain clicks and double-clicks keep their normal meaning
// (focus, select a word). mousedown is cancelled with shift held so the browser does not extend a text selection.
document.addEventListener('mousedown', ev => {
  if (ev.shiftKey && ev.target.closest && ev.target.closest('.pane')) ev.preventDefault();
});
document.addEventListener('click', ev => {
  const pane = ev.target.closest && ev.target.closest('.pane');
  if (pane && ev.shiftKey) { ev.preventDefault(); run('modal ' + pane.dataset.id); }
});

// tmux-style ctrl+b prefix -> just another POST /exec
const keymap = {'%': 'split h', '"': 'split v', '|': 'split h', '-': 'split v', 'x': 'close', 'o': 'focus',
                'w': 'ps', 's': 'screens', 'c': 'color', 'p': 'palette',
                'ArrowLeft': 'focus left', 'ArrowRight': 'focus right', 'ArrowUp': 'focus up', 'ArrowDown': 'focus down'};
const repeatable = new Set(['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'o']);  // like tmux `bind -r`: prefix stays armed briefly
let prefix = false, prefixTimer = null;
const setPrefix = (on, ms = 0) => {
  prefix = on; clearTimeout(prefixTimer);
  const h = document.getElementById('hint'); if (h) h.style.color = on ? 'var(--accent)' : '';
  if (on && ms) prefixTimer = setTimeout(() => setPrefix(false), ms);
};
const run = line => htmx.ajax('POST', '/exec', {target: '#screen', swap: 'outerHTML', headers: {'X-Client': cid},
  values: {line, quiet: /^(split|close|focus|color|palette|modal\w*)\b/.test(line) ? '1' : ''}});   // layout keys don't echo into the pane
document.addEventListener('keydown', ev => {
  if (['Shift', 'Control', 'Alt', 'Meta', 'AltGraph', 'CapsLock'].includes(ev.key)) return;  // `%` is Shift+5: don't eat the Shift
  if (ev.key === 'Escape' && !prefix) {            // Esc closes the window (back into its box)
    const m = document.querySelector('.modal[data-top="1"]');   // Esc closes the top window
    if (m) { ev.preventDefault(); ev.stopPropagation(); run('modal ' + m.dataset.pane); }
    return;
  }
  if (prefix) {
    const line = keymap[ev.key];
    setPrefix(false);
    if (line) {
      ev.preventDefault(); ev.stopPropagation();
      run(line);
      if (repeatable.has(ev.key)) setPrefix(true, 600);   // ctrl+b ← ← → : no need to retype the prefix
    }
  } else if (ev.ctrlKey && ev.key.toLowerCase() === 'b') { ev.preventDefault(); ev.stopPropagation(); setPrefix(true); }
}, true);   // capture phase: runs before the command bar's own arrow-key history handling
</script>
""".replace("__HTMX__", HTMX_SRC)

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--resume", nargs="?", const="default", metavar="NAME", help="restore a snapshot at startup")
    a = ap.parse_args()
    if a.selftest:
        selftest()
    else:
        if a.resume:
            try:
                print("resumed", app.load_file(a.resume, wipe=True)[0])
            except CmdError as e:
                print(e)
        print(f"ctxmux on http://127.0.0.1:{a.port}")
        serve(a.port).serve_forever()