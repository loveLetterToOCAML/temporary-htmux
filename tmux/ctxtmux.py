#!/usr/bin/env python3
"""ctxmux_htmx: htmx front-end for ctxmux.py. The server renders HTML; htmx just swaps it in.

    python ctxmux_htmx.py                # http://127.0.0.1:8765
    python ctxmux_htmx.py --selftest     # drives the endpoints with httpx

Routes
    GET  /                page (shell + current screen, server-rendered)
    GET  /screen?since=N  the screen fragment, or 204 (htmx swaps nothing) if rev is still N
    POST /exec            form field `line`; runs the command, returns the screen fragment

Windows: shift+click a pane -> `modal <id>` floats it (geometry saved per pane); again -> back in its box.
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
clients: dict[str, dict] = {}  # browser-tab id -> view state: current screen, window (modal) pane, window reduced?
LOCAL_HTMX = Path(__file__).with_name("htmx.min.js")
HTMX_SRC = "/htmx.min.js" if LOCAL_HTMX.exists() else "https://cdn.jsdelivr.net/npm/htmx.org@2.0.4/dist/htmx.min.js"


@contextmanager
def as_client(cid: str):
    """Run with this browser tab's view state loaded into `app` (current screen, open window), saved afterwards.
    Screens, panes and contexts stay shared; only what *this tab is looking at* is per tab."""
    st = clients.setdefault(cid, {"cur": "main", "modal": None, "modal_screen": None, "mini": False})
    app.cur = st["cur"] if st["cur"] in app.screens else next(iter(app.screens))
    app.modal, app.modal_screen, app.mini = st["modal"], st["modal_screen"], st["mini"]
    try:
        yield
    finally:
        st.update(cur=app.cur, modal=app.modal, modal_screen=app.modal_screen, mini=app.mini)


def _pane(pid: int | None) -> Pane | None:
    return next((p for p in panes(app.screen.root) if p.id == pid), None)


def _modal_pane() -> Pane | None:
    """The pane in this tab's window, if the window belongs to the screen being shown (checked per render, so a
    screen switch never shows a stale window, not even for one response)."""
    return _pane(app.modal) if app.modal is not None and app.modal_screen == app.cur else None


@command("modal")
class Modal(Command):
    """Show a pane in a window (shift+click a pane). The same command on that pane puts it back in its box."""
    id: int | None = Field(None, description="pane id (default: the focused pane)", json_schema_extra={"positional": True})
    mini: bool = Field(False, description="reduce / expand the open window to its title bar")

    def run(self, app):
        if self.mini:
            if _modal_pane() is None:
                raise CmdError("no window is open")
            app.mini = not app.mini
            return
        pid = self.id if self.id is not None else app.pane.id
        pane = _pane(pid)
        if pane is None:
            raise CmdError(f"no pane {pid}")
        app.screen.focus = pane  # the window's pane is the one you type into
        closing = _modal_pane() is pane
        app.modal, app.modal_screen, app.mini = (None, None, False) if closing else (pid, app.cur, False)


@command("modalbox")
class ModalBox(Command):
    """Remember position and size of the open window for its pane (the page sends this after you move/resize it)."""
    x: int
    y: int
    w: int
    h: int

    def run(self, app):
        pane = _modal_pane()
        if pane is not None:
            pane.box = clamp_box(self.x, self.y, self.w, self.h)


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
    in_modal = _modal_pane() is n
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


def modal_html() -> str:
    """The floating window: live content of a pane, which stays in the layout greyed out. Rendered with the screen,
    so it updates with every swap; geometry is saved per pane (and in snapshots)."""
    pane = _modal_pane()
    if pane is None:
        app.modal = app.modal_screen = None
        return ""
    c, col = app.contexts[pane.ctx], app.screen.color_of(pane)
    style = ""
    if pane.box:
        x, y, bw, bh = pane.box  # keep at least the title bar reachable if the browser window got smaller
        style += f"left:min({x}px,calc(100vw - 80px));top:min({y}px,calc(100vh - 40px));width:{bw}px;height:{bh}px;"
    if col:
        style += f"--pc:{visible(col)};"
    cls = ' class="mini"' if app.mini else ""
    st = f' style="{style}"' if style else ""
    mini_btn = post_attrs("modal --mini", quiet=True)
    close_btn = post_attrs(f"modal {pane.id}", quiet=True)
    return (f'<div id="modal" data-pane="{pane.id}"{cls}{st}>'
            f'<div class="mhead"><span>{pane.id} {escape(c.params.label())} <i>{c.id[:8]}</i></span>'
            f'<button {mini_btn} title="{"expand" if app.mini else "reduce"}">{"▢" if app.mini else "–"}</button>'
            f'<button {close_btn} title="back into its box">×</button></div>'
            f'<pre>{escape(chr(10).join(pane.buf))}</pre><div class="grip"></div></div>')


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
           f'o next &nbsp; ←↑↓→ pane &nbsp; c colour &nbsp; p palette &nbsp; shift+click: window</span></div>{modal_html()}</div>')
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
        # ---- window (modal): shift+click a pane -> `modal <id>`; geometry saved per pane; per-tab state
        F, G = {"X-Client": "tabF"}, {"X-Client": "tabG"}
        pf = lambda line, **kw: c.post("/exec", data={"line": line, **kw}, headers=F).text
        pg = lambda line, **kw: c.post("/exec", data={"line": line, **kw}, headers=G).text
        is_open = lambda h: 'id="modal"' in h
        pane_html = lambda h, i: re.search(rf'<div class="pane[^"]*" data-id="{i}".*?(?=<div class="pane|<div class="bar"|</div><div id="tabs">)', h, re.S).group(0)
        pf("screen win"); pf("ps"); h = pf("split h")             # pane 1 has output, pane 2 is focused
        unfocused = re.search(r'<div class="pane" data-id="1"[^>]*>', h).group(0)
        print("unfocused pane: click focuses, shift+click does not:", 'hx-trigger="click[!shiftKey]"' in unfocused and "focus --id 1" in unfocused)
        h = pf("modal 1", quiet="1")
        print("opens, shows pane 1 live content:", is_open(h) and 'data-pane="1"' in h and "$ ps" in h.split('id="modal"')[1])
        print("pane 1 greyed with placeholder:", "in-modal" in pane_html(h, 1) and "bring it back" in pane_html(h, 1) and "<pre>" not in pane_html(h, 1),
              "| pane 2 untouched:", "in-modal" not in pane_html(h, 2) and "<pre>" in pane_html(h, 2))
        print("opening focuses that pane:", 'class="pane focus in-modal"' in h)
        h = pf("modal 1", quiet="1")
        print("same command again puts it back:", not is_open(h) and "$ ps" in pane_html(h, 1) and "in-modal" not in h)
        pf("modal 2", quiet="1")
        h = pf("modalbox 100 50 640 400", quiet="1")
        print("geometry applied:", "left:min(100px" in h and "top:min(50px" in h and "width:640px;height:400px" in h)
        h = pf("modalbox -5 3 10 10", quiet="1")
        print("clamped (>=0, min 260x140):", "left:min(0px" in h and "width:260px;height:140px" in h)
        pf("modalbox 100 50 640 400", quiet="1"); pf("modal 2", quiet="1")           # close
        h = pf("modal 2", quiet="1")
        print("size saved per pane, restored on reopen:", "width:640px;height:400px" in h)
        h = pf("modal 1", quiet="1")
        print("another pane has its own default (no inline geometry):", "width:640px" not in h.split('id="modal"')[1].split(">")[0])
        h = pf("modal --mini", quiet="1")
        print("reduce / expand:", 'class="mini"' in h, "->", 'class="mini"' not in pf("modal --mini", quiet="1"))
        print("per tab: tab G (same screen) has no window:", not is_open(pg("screen win")), "| F still has one:", is_open(pf("ps")))
        print("leaving the screen closes the window:", not is_open(pf("screen main")) and not is_open(pf("screen win")))
        print("errors: unknown pane:", "no pane 99" in pf("modal 99"), "| --mini with none open:", "no window is open" in pf("modal --mini"))
        pf("modal 2", quiet="1"); pf("modalbox 120 60 500 300", quiet="1"); pf("save wintest")
        h = pf("restore wintest --wipe")                # same screen stays current, so the window is still open
        print("geometry survives save/restore:", is_open(h) and "left:min(120px" in h and "width:500px;height:300px" in h)
        pf("close")                                    # closing the pane shown in a window must not leave a ghost window
        print("closing the pane drops its window:", not is_open(pf("ps")))
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
  #modal { position: fixed; z-index: 50; left: max(10vw, calc(50vw - 450px)); top: max(15vh, calc(50vh - 260px));
           width: min(900px, 80vw); height: min(520px, 70vh); max-width: calc(100vw - 8px); max-height: calc(100vh - 8px);
           display: flex; flex-direction: column; background: var(--bg); border: 2px solid var(--pc, var(--accent));
           border-radius: 8px; box-shadow: 0 14px 44px rgba(0,0,0,.65); }
  #modal.mini { height: auto !important; } #modal.mini pre, #modal.mini .grip { display: none; }
  .mhead { display: flex; align-items: center; gap: 4px; padding: 4px 6px 4px 10px; cursor: move; user-select: none;
           border-bottom: 1px solid var(--line); color: var(--pc, var(--accent)); }
  #modal.mini .mhead { border-bottom: 0; }
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

// window (modal): move by its title bar, resize with the corner grip; geometry is saved per pane on release.
document.addEventListener('pointerdown', ev => {
  const m = ev.target.closest && ev.target.closest('#modal');
  const head = m && ev.target.closest('.mhead'), grip = m && ev.target.closest('.grip');
  if (!m || ev.target.closest('button') || !(head || grip)) return;
  mdrag = {m, size: !!grip, ox: ev.clientX, oy: ev.clientY, r: m.getBoundingClientRect()};
  (grip || head).setPointerCapture(ev.pointerId); ev.preventDefault();
});
document.addEventListener('pointermove', ev => {
  if (!mdrag) return;
  const {m, size, ox, oy, r} = mdrag, dx = ev.clientX - ox, dy = ev.clientY - oy;
  if (size) { m.style.width = Math.max(260, r.width + dx) + 'px'; m.style.height = Math.max(140, r.height + dy) + 'px'; }
  else { m.style.left = Math.max(0, r.left + dx) + 'px'; m.style.top = Math.max(0, r.top + dy) + 'px'; }
});
document.addEventListener('pointerup', () => {
  if (!mdrag) return;
  const {m, r} = mdrag, n = m.getBoundingClientRect(); mdrag = null;
  if ([n.left - r.left, n.top - r.top, n.width - r.width, n.height - r.height].some(v => Math.abs(v) > 2))
    run(`modalbox ${Math.round(n.left)} ${Math.round(n.top)} ${Math.round(n.width)} ${Math.round(n.height)}`);
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
    const m = document.getElementById('modal');
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