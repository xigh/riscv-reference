#!/usr/bin/env python3
"""Generate a static RISC-V reference site from riscv-unified-db.

Usage: python3 build.py [--udb udb] [--out build]

One page per instruction, CSR and extension, plus index pages. Every link is
relative, so the same output works at a domain root, under /<repo>/ on GitHub
Pages, or in any sub-directory of another site. Search is added afterwards by
Pagefind (see README).
"""

import argparse
import collections
import datetime
import glob
import html
import json
import os
import re
import shutil
import subprocess
import tempfile
import sys

import yaml

import erb
import profiles
import specs

HERE = os.path.dirname(os.path.abspath(__file__))
SITE_TITLE = "RISC-V ISA Reference"

# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------


def load_kind(isa, kind):
    out = {}
    for path in sorted(glob.glob(os.path.join(isa, kind, "**", "*.yaml"), recursive=True)):
        with open(path, encoding="utf-8") as f:
            d = yaml.safe_load(f)
        name = d["name"]
        if name in out:
            sys.exit(f"duplicate {kind} name {name!r}: {path}")
        d["_path"] = os.path.relpath(path, isa)
        out[name] = d
    # Page file names must not collide on case-insensitive file systems.
    low = collections.Counter(n.lower() for n in out)
    clash = [n for n, c in low.items() if c > 1]
    if clash:
        sys.exit(f"{kind}: names differing only by case: {clash}")
    return out


def load_list(isa, kind):
    out = []
    for path in sorted(glob.glob(os.path.join(isa, kind, "*.yaml"))):
        with open(path, encoding="utf-8") as f:
            out.append(yaml.safe_load(f))
    return out


# --------------------------------------------------------------------------
# Minimal AsciiDoc -> HTML (only the constructs present in UDB descriptions)
# --------------------------------------------------------------------------

XREF = re.compile(r"<<([A-Za-z0-9_:.\-]+)(?:,([^>]*))?>>")
URLTXT = re.compile(r"(?:link:)?(https?://[^\s\[\]<>]+)\[([^\]]*)\]")
BAREURL = re.compile(r"(?<![\"'=])\b(https?://[^\s\[\]<>]+[^\s\[\]<>.,;:)])")
CODE = re.compile(r"`([^`]+)`")
BOLD = re.compile(r"(?<![\w*])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![\w*])")
ITAL = re.compile(r"(?<![\w_])_(?=\S)([^_\n]+?)(?<=\S)_(?![\w_])")


class Ctx:
    """Rendering context: where the page lives and what names exist."""

    def __init__(self, root, names, prose=None):
        self.root = root  # relative prefix to site root, e.g. "../"
        self.names = names  # {"ext": set, "csr": set, "inst": set}
        self.prose = prose

    def link(self, kind, name, text=None):
        text = html.escape(text if text is not None else name)
        if name in self.names.get(kind, ()):
            return f'<a href="{self.root}{kind}/{name}.html">{text}</a>'
        return text


def inline(text, ctx):
    slots = []

    def keep(s):
        slots.append(s)
        return f"\x00{len(slots) - 1}\x00"

    def code(m):
        return keep(f"<code>{html.escape(m.group(1))}</code>")

    def xref(m):
        target, label = m.group(1), m.group(2)
        label = label.strip() if label else target
        for kind in ("ext", "csr", "inst", "profile"):
            if target in ctx.names.get(kind, ()):
                return keep(ctx.link(kind, target, label))
        return keep(f"<em>{html.escape(label)}</em>")

    def urltxt(m):
        url, label = m.group(1), m.group(2) or m.group(1)
        return keep(f'<a href="{html.escape(url)}">{html.escape(label)}</a>')

    def bare(m):
        url = m.group(1)
        return keep(f'<a href="{html.escape(url)}">{html.escape(url)}</a>')

    text = CODE.sub(code, text)
    text = XREF.sub(xref, text)
    text = URLTXT.sub(urltxt, text)
    text = BAREURL.sub(bare, text)
    text = html.escape(text, quote=False)
    text = re.sub(r"&amp;([A-Za-z]+|#\d+|#x[0-9A-Fa-f]+);", r"&\1;", text)  # source entities
    text = BOLD.sub(r"<strong>\1</strong>", text)
    text = ITAL.sub(r"<em>\1</em>", text)
    text = text.replace("{nbsp}", "&nbsp;")
    return re.sub(r"\x00(\d+)\x00", lambda m: slots[int(m.group(1))], text)


TEX = {r"\infty": "∞", r"\le": "≤", r"\ge": "≥", r"\times": "×", r"\cdot": "·", r"\pm": "±", r"\ne": "≠"}


def latex(m):
    t = m.group(1).strip().strip("$")
    for k, v in TEX.items():
        t = t.replace(k, v)
    return t.replace("{", "").replace("}", "")


def fix_bang_tables(src):
    """UDB sometimes opens a top-level table with "!===" but omits the
    [separator="!"] attribute (and mixes "|" in the header row). Add it."""
    lines = src.split("\n")
    out, in_pipe, in_bang = [], False, False
    for i, line in enumerate(lines):
        s = line.strip()
        if s == "|===":
            in_pipe = not in_pipe
        elif s == "!===" and not in_pipe:
            if not in_bang:
                prev = next((l for l in reversed(out) if l.strip()), "")
                if "separator=" not in prev:
                    out.append('[separator="!"]')
                    in_bang = "fix"
                else:
                    in_bang = True
            else:
                in_bang = False
        elif in_bang == "fix" and s.startswith("!"):
            line = re.sub(r"(^|\s)\|", r"\1!", line)
        out.append(line)
    return "\n".join(out)


def when(m):
    """UDB's [when,"COND"] attribute -> a visible "When COND:" label."""
    label = f"_When_ `{m.group(1)}`:"
    if m.group(2) and "--" in m.group(2):  # applies to an open block: keep it
        return label + "\n\n--\n"
    return label + " "


class Prose:
    """Collects AsciiDoc snippets; converted in one Asciidoctor run at the end."""

    def __init__(self, codes):
        self.codes = codes
        self.snippets = []
        self.where = []  # source file of each snippet, for diagnostics
        self.current = ""

    def __call__(self, src):
        if not src:
            return ""
        src = erb.resolve(str(src), self.codes)
        src = re.sub(r"(?m)^\s*include::.*\[\]\s*$", "", src)
        src = re.sub(r'\[when,\s*"([^"]*)"\][ \t]*(\n[ \t]*--[ \t]*\n|\n)?', when, src)
        src = re.sub(r"latexmath:\[(.*?)\]", latex, src)
        src = fix_bang_tables(src)
        src = re.sub(r"xref:[\w:./-]+\[([^\]]*)\]", r"\1", src)
        self.snippets.append(src)
        self.where.append(self.current)
        return f"\x01P{len(self.snippets) - 1}\x01"

    def convert(self, tmpdir):
        src = os.path.join(tmpdir, "prose-in.json")
        dst = os.path.join(tmpdir, "prose-out.json")
        with open(src, "w", encoding="utf-8") as f:
            json.dump(self.snippets, f)
        res = subprocess.run(["node", os.path.join(HERE, "adoc.mjs"), src, dst],
                             stderr=subprocess.PIPE, text=True)
        if res.returncode:
            sys.exit(f"adoc.mjs failed:\n{res.stderr}")
        for line in res.stderr.splitlines():
            m = re.match(r"snippet (\d+): (.*)", line)
            print(f"asciidoctor: {self.where[int(m.group(1))]}: {m.group(2)}" if m else line, file=sys.stderr)
        with open(dst, encoding="utf-8") as f:
            self.html = json.load(f)
        if len(self.html) != len(self.snippets):
            sys.exit("asciidoctor returned a different number of snippets")


ANCHOR = re.compile(r'<a href="([^"]*)">(.*?)</a>', re.S)


def fill(text, prose, ctx):
    """Insert converted prose and turn its cross-references into site links."""

    def link(m):
        href, label = m.group(1), m.group(2)
        if href.startswith(("http://", "https://", "mailto:")):
            return m.group(0)
        target = href.lstrip("#")
        clean = re.sub(r"^\[(.*)\]$", r"\1", label)
        for kind in ("ext", "csr", "inst"):
            if target in ctx.names[kind]:
                return f'<a href="{ctx.root}{kind}/{target}.html">{clean}</a>'
        return f"<em>{clean}</em>"

    def sub(m):
        return ANCHOR.sub(link, prose.html[int(m.group(1))])

    return re.sub(r"\x01P(\d+)\x01", sub, text)


# --------------------------------------------------------------------------
# Conditions (definedBy)
# --------------------------------------------------------------------------


def ext_names(cond):
    """Extension names a definedBy condition mentions (outside parameter tests)."""
    found = set()

    def walk(x, in_ext):
        if isinstance(x, dict):
            if in_ext and isinstance(x.get("name"), str):
                found.add(x["name"])
            for k, v in x.items():
                if k == "param":
                    continue
                walk(v, in_ext or k == "extension")
        elif isinstance(x, list):
            for v in x:
                walk(v, in_ext)

    walk(cond, False)
    return found


def ext_req(e, ctx):
    if "name" in e:
        s = ctx.link("ext", e["name"])
        if "version" in e:
            s += f' <span class="ver">{html.escape(str(e["version"]))}</span>'
        return s
    return combine(e, ctx, ext_req)


def combine(x, ctx, leaf):
    for key, word in (("allOf", " and "), ("anyOf", " or "), ("oneOf", " xor "), ("noneOf", " nor ")):
        if key in x:
            parts = [leaf(p, ctx) for p in x[key]]
            if key == "noneOf":
                return "none of (" + ", ".join(parts) + ")"
            if len(parts) == 1:
                return parts[0]
            return "(" + word.join(parts) + ")"
    if "not" in x:
        return "not " + leaf(x["not"], ctx)
    if "if" in x and "then" in x:
        return f"(if {strip_parens(leaf(x['if'], ctx))} then {strip_parens(leaf(x['then'], ctx))})"
    raise ValueError(f"unhandled condition: {x!r}")


PARAM_OPS = (("equal", "="), ("greaterThanOrEqual", "≥"), ("greaterThan", ">"),
             ("lessThanOrEqual", "≤"), ("lessThan", "<"), ("includes", "includes"))


def param_cond(p, ctx):
    if "name" not in p:
        return combine(p, ctx, param_cond)
    for key in ("oneOf", "anyOf"):
        if isinstance(p.get(key), list) and all(not isinstance(v, (dict, list)) for v in p[key]):
            vals = ", ".join(str(v) for v in p[key])
            s = f"<code>{html.escape(p['name'])} ∈ {{{html.escape(vals)}}}</code>"
            break
    else:
        s = None
    for key, op in PARAM_OPS if s is None else ():
        if key in p:
            val = p[key]
            val = str(val).lower() if isinstance(val, bool) else str(val)
            s = f"<code>{html.escape(p['name'])} {op} {html.escape(val)}</code>"
            break
    else:
        if s is None:
            raise ValueError(f"unhandled parameter test: {p!r}")
    if p.get("reason"):
        s += f' <span class="ver">({html.escape(str(p["reason"]))})</span>'
    return s


def condition(c, ctx):
    if "extension" in c:
        return ext_req(c["extension"], ctx)
    if "xlen" in c:
        return f"RV{c['xlen']}"
    if "param" in c:
        return param_cond(c["param"], ctx)
    if "idl()" in c:
        r = f' ({html.escape(str(c["reason"]))})' if c.get("reason") else ""
        return f"<code>{html.escape(str(c['idl()']).strip())}</code>{r}"
    if "name" in c:  # bare extension reference
        return ext_req(c, ctx)
    return combine(c, ctx, condition)


def strip_parens(s):
    return s[1:-1] if s.startswith("(") and s.endswith(")") else s


# --------------------------------------------------------------------------
# Instruction encodings
# --------------------------------------------------------------------------


def parse_location(loc):
    ranges = []
    for part in str(loc).split("|"):
        part = part.strip()
        if "-" in part:
            hi, lo = (int(p) for p in part.split("-"))
        else:
            hi = lo = int(part)
        if hi < lo:
            raise ValueError(f"bad location {loc!r}")
        ranges.append((hi, lo))
    return ranges


def encoding_table(enc):
    match = enc["match"]
    width = len(match)
    label = [None] * width  # by bit position
    for i, ch in enumerate(match):
        if ch in "01":
            label[width - 1 - i] = ("c", ch)
        elif ch != "-":
            raise ValueError(f"bad match char {ch!r}")
    for v in enc.get("variables") or []:
        ranges = parse_location(v["location"])
        shift = int(v.get("left_shift", 0))
        vb = sum(hi - lo + 1 for hi, lo in ranges) - 1 + shift
        for hi, lo in ranges:
            for pos in range(hi, lo - 1, -1):
                if label[pos] is not None:
                    raise ValueError(f"bit {pos} defined twice in {match}")
                label[pos] = ("v", v["name"], vb, len(ranges) == 1 and shift == 0)
                vb -= 1
    cells = []  # (hi_pos, lo_pos, text, css)
    for pos in range(width - 1, -1, -1):
        lab = label[pos]
        prev = cells[-1] if cells else None
        if lab is None:
            lab = ("u",)
        if prev and prev[4][0] == lab[0] and (
            lab[0] in ("c", "u") or (lab[1] == prev[4][1] and lab[2] == prev[4][2] - 1)
        ):
            prev[1] = pos
            prev[3].append(lab)
            prev[4] = lab
            continue
        cells.append([pos, pos, None, [lab], lab])
    tds = []
    for hi, lo, _, labs, _ in cells:
        span = hi - lo + 1
        kind = labs[0][0]
        if kind == "c":
            text, css = "".join(l[1] for l in labs), "fixed"
        elif kind == "u":
            text, css = "-" * span, "unused"
        else:
            name, top, whole = labs[0][1], labs[0][2], labs[0][3]
            bot = labs[-1][2]
            idx = "" if whole else (f"[{top}]" if top == bot else f"[{top}:{bot}]")
            css = "var"
            if idx and span * 2 < len(name) + len(idx):  # too narrow for one line
                tds.append(f'<td colspan="{span}" class="var narrow">{html.escape(name)}<br>{html.escape(idx)}</td>')
                continue
            text = name + idx
        tds.append(f'<td colspan="{span}" class="{css}">{html.escape(text)}</td>')
    head = "".join(f"<th>{p}</th>" for p in range(width - 1, -1, -1))
    return (
        f'<div class="enc-wrap" data-pagefind-ignore><table class="enc enc{width}"><tr class="bits">{head}</tr>'
        f'<tr>{"".join(tds)}</tr></table></div>'
    )


def variables_table(enc):
    rows = []
    for v in enc.get("variables") or []:
        notes = []
        if "not" in v:
            notes.append(f"≠ {v['not']}")
        if v.get("sign_extend"):
            notes.append("sign-extended")
        if v.get("left_shift"):
            notes.append(f"&lt;&lt; {int(v['left_shift'])}")
        rows.append(
            f"<tr><td><code>{html.escape(v['name'])}</code></td>"
            f"<td><code>{html.escape(str(v['location']))}</code></td><td>{', '.join(notes)}</td></tr>"
        )
    if not rows:
        return ""
    return (
        '<table class="vars" data-pagefind-ignore><tr><th>Field</th><th>Bits</th><th>Notes</th></tr>'
        + "".join(rows)
        + "</table>"
    )


# --------------------------------------------------------------------------
# Page shell
# --------------------------------------------------------------------------


def page(ctx, title, body, desc=""):
    r = ctx.root
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title.replace("`", ""))} — {SITE_TITLE}</title>
<meta name="description" content="{html.escape((desc or title).replace("`", ""))}">
<link rel="icon" href="{r}static/favicon.svg">
<link rel="stylesheet" href="{r}static/style.css">
<link rel="stylesheet" href="{r}pagefind/pagefind-ui.css">
</head>
<body>
<header data-pagefind-ignore="all">
<a class="brand" href="{r}index.html">{SITE_TITLE}</a>
<nav><a href="{r}inst/index.html">Instructions</a> <a href="{r}csr/index.html">CSRs</a> <a href="{r}ext/index.html">Extensions</a> <a href="{r}profile/index.html">Profiles</a> <a href="{r}specs.html">Specifications</a> <a href="{r}about.html">About</a></nav>
<div id="search" data-root="{r}"></div>
</header>
<main data-pagefind-body>
{body}
</main>
<footer data-pagefind-ignore="all">
Generated from <a href="https://github.com/riscv-software-src/riscv-unified-db">riscv-unified-db</a>
(BSD-3-Clause-Clear, © Qualcomm Technologies, Inc. and contributors). Unofficial; see <a href="{r}about.html">About</a>.
</footer>
<script src="{r}pagefind/pagefind-ui.js"></script>
<script src="{r}static/search.js"></script>
</body>
</html>
"""


PENDING = []  # pages are written once the prose has been converted


def write(out, rel, text):
    PENDING.append((out, rel, text))


def flush(prose, names):
    for out, rel, text in PENDING:
        ctx = Ctx("../" * rel.count("/"), names)
        path = os.path.join(out, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(fill(text, prose, ctx))


def latest_version(ext):
    vs = ext.get("versions") or []
    return vs[-1] if vs else {}


def state_badge(state):
    if not state:
        return ""
    return f'<span class="state state-{html.escape(state)}">{html.escape(state)}</span>'


def idl_block(title, code, open_=False):
    if not code:
        return ""
    o = " open" if open_ else ""
    return f'<details{o}><summary>{html.escape(title)}</summary><pre class="idl">{html.escape(str(code).rstrip())}</pre></details>'


# --------------------------------------------------------------------------
# Search filters (Pagefind)
# --------------------------------------------------------------------------

EXT_STATE = {}  # extension name -> state of its latest version, filled in main()
EXT_TYPE = {}  # extension name -> "privileged" / "unprivileged", filled in main()
SNAP = {}  # data/specs.json
SPEC_REG = {}  # extension name -> what RISC-V International says about it
PROFILES = {}  # profile release -> extension names it lists (oldest release first)


def xlens(cond):
    found = set()

    def walk(x):
        if isinstance(x, dict):
            if "xlen" in x:
                found.add(int(x["xlen"]))
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk(cond)
    return found or {32, 64}


def filters(**kw):
    """Hidden elements Pagefind reads as filters: key -> value or list of values."""
    spans = []
    for key, vals in kw.items():
        for v in [vals] if isinstance(vals, str) else sorted(set(vals)):
            spans.append(f'<span data-pagefind-filter="{html.escape(key)}">{html.escape(str(v))}</span>')
    return '<div class="pf-filters" hidden>' + "".join(spans) + "</div>"


# --------------------------------------------------------------------------
# Instructions
# --------------------------------------------------------------------------


def inst_page(d, ctx):
    ctx.prose.current = d["_path"]
    name = d["name"]
    b = [f'<h1><code>{html.escape(name)}</code> <span class="long">{inline(d["long_name"], ctx)}</span></h1>']
    asm = d.get("assembly", "")
    b.append(f'<pre class="asm">{html.escape(name)} {html.escape(str(asm))}</pre>')
    b.append(f'<p class="meta"><b>Defined by:</b> {strip_parens(condition(d["definedBy"], ctx))}</p>')
    enc = d["encoding"]
    b.append("<h2>Encoding</h2>")
    if "match" in enc:
        b.append(encoding_table(enc) + variables_table(enc))
    else:
        for xl in ("RV32", "RV64"):
            if xl in enc:
                b.append(f"<h3>{xl}</h3>" + encoding_table(enc[xl]) + variables_table(enc[xl]))
    b.append("<h2>Description</h2>")
    b.append(ctx.prose(d["description"]))
    acc = d.get("access") or {}
    if acc:
        rows = "".join(
            f"<tr><td>{html.escape(m.upper())}</td><td>{html.escape(str(v))}</td></tr>" for m, v in acc.items()
        )
        b.append(f'<h2>Access</h2><table class="small"><tr><th>Mode</th><th>Access</th></tr>{rows}</table>')
        if d.get("access_detail"):
            b.append(ctx.prose(d["access_detail"]))
    if "data_independent_timing" in d:
        b.append(f'<p class="meta"><b>Data-independent timing:</b> {"yes" if d["data_independent_timing"] else "no"}</p>')
    ps = d.get("pseudoinstructions") or []
    if ps:
        rows = "".join(
            f"<tr><td><code>{html.escape(str(p.get('to', '')))}</code></td><td><code>{html.escape(str(p.get('when', '')))}</code></td></tr>"
            for p in ps
        )
        b.append(f'<h2>Pseudoinstructions</h2><table class="small"><tr><th>Pseudo</th><th>When</th></tr>{rows}</table>')
    hints = d.get("hints") or []
    if hints:
        links = []
        for h in hints:
            ref = h.get("$ref", "") if isinstance(h, dict) else str(h)
            hn = os.path.splitext(os.path.basename(ref.split("#")[0]))[0]
            links.append(ctx.link("inst", hn))
        b.append("<h2>Hints</h2><p>" + ", ".join(links) + "</p>")
    if d.get("operation()"):
        b.append("<h2>Operation</h2>" + idl_block("IDL (Instruction Definition Language)", d["operation()"], True))
    b.append(source_link(d))
    exts = ext_names(d["definedBy"])
    widths = {len(enc["match"])} if "match" in enc else {len(v["match"]) for v in enc.values() if isinstance(v, dict)}
    b.append(filters(
        type="Instruction",
        extension=exts,
        base=[f"RV{x}" for x in xlens(d["definedBy"])],
        encoding=[f"{w}-bit" for w in widths],
        state=[EXT_STATE.get(e, "unknown") for e in exts],
        category=[EXT_TYPE.get(e, "other") for e in exts],
    ))
    return page(ctx, name, "\n".join(b), f"{name}: {d['long_name']}")


def source_link(d):
    return (
        f'<p class="src">Source: <code>spec/std/isa/{html.escape(d["_path"])}</code></p>'
    )


# --------------------------------------------------------------------------
# CSRs
# --------------------------------------------------------------------------


def field_location(f):
    if "location" in f:
        return html.escape(str(f["location"]))
    parts = []
    if "location_rv32" in f:
        parts.append(f"RV32: {f['location_rv32']}")
    if "location_rv64" in f:
        parts.append(f"RV64: {f['location_rv64']}")
    return "<br>".join(html.escape(p) for p in parts)


def sort_key_location(item):
    f = item[1]
    loc = f.get("location", f.get("location_rv64", f.get("location_rv32", 0)))
    try:
        return -parse_location(loc)[0][0]
    except Exception:
        return 0


def csr_page(d, ctx):
    ctx.prose.current = d["_path"]
    name = d["name"]
    b = [f'<h1><code>{html.escape(name)}</code> <span class="long">{inline(d["long_name"], ctx)}</span></h1>']
    meta = [f"<b>Address:</b> <code>0x{int(d['address']):03x}</code>"]
    if d.get("virtual_address") is not None:
        meta.append(f"<b>Virtual address:</b> <code>0x{int(d['virtual_address']):03x}</code>")
    if d.get("indirect_address") is not None:
        meta.append(
            f"<b>Indirect address:</b> <code>0x{int(d['indirect_address']):x}</code> slot {html.escape(str(d.get('indirect_slot')))}"
        )
    meta.append(f"<b>Privilege:</b> {html.escape(str(d['priv_mode']))}")
    meta.append(f"<b>Length:</b> {html.escape(str(d['length']))}")
    meta.append(f"<b>Writable:</b> {'yes' if d.get('writable') else 'no'}")
    b.append('<p class="meta">' + " · ".join(meta) + "</p>")
    b.append(f'<p class="meta"><b>Defined by:</b> {strip_parens(condition(d["definedBy"], ctx))}</p>')
    b.append("<h2>Description</h2>" + ctx.prose(d["description"]))
    fields = sorted((d.get("fields") or {}).items(), key=sort_key_location)
    if fields:
        b.append("<h2>Fields</h2>")
        rows = ['<table class="fields"><tr><th>Bits</th><th>Field</th><th>Type</th><th>Reset</th><th>Description</th></tr>']
        for fname, f in fields:
            typ = f.get("type", "dynamic" if "type()" in f else "")
            reset = f.get("reset_value", "dynamic" if "reset_value()" in f else "")
            desc = ctx.prose(f.get("description", ""))
            extra = []
            if f.get("long_name"):
                desc = f"<p><b>{inline(f['long_name'], ctx)}</b></p>" + desc
            if f.get("definedBy"):
                extra.append(f'<p class="meta"><b>Defined by:</b> {strip_parens(condition(f["definedBy"], ctx))}</p>')
            if f.get("alias"):
                extra.append(f'<p class="meta"><b>Alias:</b> <code>{html.escape(str(f["alias"]))}</code></p>')
            extra.append(idl_block("type()", f.get("type()")))
            extra.append(idl_block("reset_value()", f.get("reset_value()")))
            extra.append(idl_block("sw_write(csr_value)", f.get("sw_write(csr_value)")))
            rows.append(
                f'<tr id="{html.escape(fname)}"><td class="nowrap">{field_location(f)}</td><td><code>{html.escape(fname)}</code></td>'
                f"<td>{html.escape(str(typ))}</td><td><code>{html.escape(str(reset))}</code></td>"
                f"<td>{desc}{''.join(extra)}</td></tr>"
            )
        rows.append("</table>")
        b.append("".join(rows))
    if d.get("sw_read()"):
        b.append("<h2>Software read</h2>" + idl_block("sw_read()", d["sw_read()"]))
    b.append(source_link(d))
    exts = ext_names(d["definedBy"])
    b.append(filters(
        type="CSR",
        extension=exts,
        privilege=str(d["priv_mode"]),
        base=[f"RV{x}" for x in xlens(d["definedBy"])],
        state=[EXT_STATE.get(e, "unknown") for e in exts],
    ))
    return page(ctx, name, "\n".join(b), f"{name}: {d['long_name']} (CSR 0x{int(d['address']):03x})")


# --------------------------------------------------------------------------
# Extensions
# --------------------------------------------------------------------------


def ext_page(d, ctx, insts, csrs):
    ctx.prose.current = d["_path"]
    name = d["name"]
    v = latest_version(d)
    b = [f'<h1>{html.escape(name)} <span class="long">{inline(d["long_name"], ctx)}</span></h1>']
    meta = [f"<b>Type:</b> {html.escape(str(d.get('type', '')))}"]
    if v:
        meta.append(f"<b>Version:</b> {html.escape(str(v.get('version', '')))} {state_badge(v.get('state'))}")
        if v.get("ratification_date"):
            meta.append(f"<b>Ratified:</b> {html.escape(str(v['ratification_date']))}")
    comp = d.get("company")
    if isinstance(comp, dict) and comp.get("name"):
        meta.append(f"<b>Company:</b> {html.escape(comp['name'])}")
    b.append('<p class="meta">' + " · ".join(meta) + "</p>")
    if d.get("requirements"):
        b.append(f'<p class="meta"><b>Requires:</b> {strip_parens(condition(d["requirements"], ctx))}</p>')
    if SPEC_REG.get(name):
        b.append(specs.spec_line(SPEC_REG[name], SNAP, ctx))
    b.append("<h2>Description</h2>" + ctx.prose(d["description"]))
    if insts:
        b.append(f"<h2>Instructions ({len(insts)})</h2>" + inst_list(insts, ctx))
    if csrs:
        b.append(f"<h2>CSRs ({len(csrs)})</h2>" + csr_list(csrs, ctx))
    vs = d.get("versions") or []
    if vs:
        rows = "".join(
            f"<tr><td>{html.escape(str(x.get('version', '')))}</td><td>{state_badge(x.get('state'))}</td>"
            f"<td>{html.escape(str(x.get('ratification_date', '') or ''))}</td></tr>"
            for x in vs
        )
        b.append(f'<h2>Versions</h2><table class="small"><tr><th>Version</th><th>State</th><th>Ratified</th></tr>{rows}</table>')
        repos = [r.get("url") for x in vs for r in (x.get("repositories") or []) if r.get("url")]
        if repos:
            b.append("<p>Specification sources: " + ", ".join(
                f'<a href="{html.escape(u)}">{html.escape(u)}</a>' for u in dict.fromkeys(repos)) + "</p>")
    b.append(source_link(d))
    b.append(filters(type="Extension", extension=name, state=v.get("state", "unknown"),
                     category=str(d.get("type", "other"))))
    return page(ctx, name, "\n".join(b), f"{name}: {d['long_name']}")


def inst_list(insts, ctx):
    rows = "".join(
        f'<tr><td>{ctx.link("inst", n)}</td><td>{inline(d["long_name"], ctx)}</td></tr>' for n, d in insts
    )
    return f'<table class="list"><tr><th>Mnemonic</th><th>Name</th></tr>{rows}</table>'


def csr_list(csrs, ctx):
    rows = "".join(
        f'<tr><td><code>0x{int(d["address"]):03x}</code></td><td>{ctx.link("csr", n)}</td>'
        f'<td>{html.escape(str(d["priv_mode"]))}</td><td>{inline(d["long_name"], ctx)}</td></tr>'
        for n, d in csrs
    )
    return f'<table class="list"><tr><th>Address</th><th>Name</th><th>Priv</th><th>Description</th></tr>{rows}</table>'


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--udb", default=os.path.join(HERE, "udb"))
    ap.add_argument("--out", default=os.path.join(HERE, "build"))
    a = ap.parse_args()
    isa = os.path.join(a.udb, "spec", "std", "isa")
    insts, csrs, exts = (load_kind(isa, k) for k in ("inst", "csr", "ext"))
    P = profiles.Profiles(isa)
    names = {"inst": set(insts), "csr": set(csrs), "ext": set(exts), "profile": set(P.names)}
    commit_file = os.path.join(HERE, "UDB_COMMIT")
    commit = open(commit_file).read().strip() if os.path.exists(commit_file) else "unknown"

    if os.path.exists(a.out):
        shutil.rmtree(a.out)
    shutil.copytree(os.path.join(HERE, "static"), os.path.join(a.out, "static"))

    codes = {
        "implemented_exception_codes": load_list(isa, "exception_code"),
        "implemented_interrupt_codes": load_list(isa, "interrupt_code"),
    }
    prose = Prose(codes)
    for path in sorted(glob.glob(os.path.join(isa, "profile", "*.yaml"))):
        with open(path, encoding="utf-8") as f:
            raw = f.read()
        prof = yaml.safe_load(raw)
        release = re.match(r"RV[A-Z]\d+", prof["name"]).group(0)
        named = {k for k in (prof.get("extensions") or {}) if not k.startswith("$")}
        named |= {n for n in exts if re.search(rf"\b{re.escape(n)}\b", raw)}  # named in the prose too
        PROFILES.setdefault(release, set()).update(named)
    order = ["RVI20", "RVA20", "RVA22", "RVB23", "RVA23"]
    for k in sorted(PROFILES, key=lambda r: order.index(r) if r in order else 99):
        PROFILES[k] = PROFILES.pop(k)
    snap_path = os.path.join(HERE, "data", "specs.json")
    if os.path.exists(snap_path):
        with open(snap_path, encoding="utf-8") as f:
            SNAP.update(json.load(f))
        SPEC_REG.update(specs.registry(SNAP, exts, PROFILES))
    EXT_TYPE.update((n, str(d.get("type", "other"))) for n, d in exts.items())
    EXT_STATE.update((n, str(latest_version(d).get("state", "unknown"))) for n, d in exts.items())
    sub = Ctx("../", names, prose)
    top = Ctx("", names, prose)
    by_ext_inst = collections.defaultdict(list)
    by_ext_csr = collections.defaultdict(list)

    for n, d in sorted(insts.items()):
        write(a.out, f"inst/{n}.html", inst_page(d, sub))
        for e in ext_names(d["definedBy"]):
            by_ext_inst[e].append((n, d))
    for n, d in sorted(csrs.items()):
        write(a.out, f"csr/{n}.html", csr_page(d, sub))
        for e in ext_names(d["definedBy"]):
            by_ext_csr[e].append((n, d))
    csr_by_addr = lambda items: sorted(items, key=lambda x: (int(x[1]["address"]), x[0]))
    for n, d in sorted(exts.items()):
        write(a.out, f"ext/{n}.html", ext_page(d, sub, by_ext_inst.get(n, []), csr_by_addr(by_ext_csr.get(n, []))))

    # Index: instructions grouped by their primary extension (UDB directory)
    groups = collections.defaultdict(list)
    for n, d in sorted(insts.items()):
        groups[d["_path"].split(os.sep)[1]].append((n, d))
    toc = " ".join(f'<a href="#{html.escape(g)}">{html.escape(g)}</a>' for g in sorted(groups, key=str.lower))
    body = [f"<h1>Instructions ({len(insts)})</h1>", f'<p class="toc">{toc}</p>']
    for g in sorted(groups, key=str.lower):
        title = sub.link("ext", g)
        if g in exts:
            title += f' <span class="long">{inline(exts[g]["long_name"], sub)}</span>'
        body.append(f'<h2 id="{html.escape(g)}">{title}</h2>' + inst_list(groups[g], sub))
    write(a.out, "inst/index.html", page(sub, "Instructions", "\n".join(body)))

    write(a.out, "csr/index.html", page(sub, "CSRs", f"<h1>Control and Status Registers ({len(csrs)})</h1>" + csr_list(csr_by_addr(csrs.items()), sub)))

    by_type = collections.defaultdict(list)
    for n, d in exts.items():
        by_type[str(d.get("type", "other"))].append((n, d))
    body = [f"<h1>Extensions ({len(exts)})</h1>"]
    for t in sorted(by_type):
        rows = "".join(
            f'<tr><td>{sub.link("ext", n)}</td><td>{inline(d["long_name"], sub)}</td>'
            f'<td>{html.escape(str(latest_version(d).get("version", "")))}</td><td>{state_badge(latest_version(d).get("state"))}</td>'
            f'<td class="num">{len(by_ext_inst.get(n, []))}</td><td class="num">{len(by_ext_csr.get(n, []))}</td></tr>'
            for n, d in sorted(by_type[t], key=lambda x: x[0].lower())
        )
        body.append(
            f"<h2>{html.escape(t.capitalize())}</h2>"
            f'<table class="list"><tr><th>Name</th><th>Description</th><th>Version</th><th>State</th><th>Insts</th><th>CSRs</th></tr>{rows}</table>'
        )
    write(a.out, "ext/index.html", page(sub, "Extensions", "\n".join(body)))

    today = datetime.date.today().isoformat()
    home = f"""
<h1>{SITE_TITLE}</h1>
<p>Reference pages for the RISC-V instruction set: every instruction with its encoding, assembly syntax,
description and formal operation, every control and status register with its fields, and every extension
with what it defines. Use the search field above, or browse:</p>
<ul class="big">
<li><a href="inst/index.html">Instructions</a> ({len(insts)})</li>
<li><a href="csr/index.html">Control and Status Registers</a> ({len(csrs)})</li>
<li><a href="ext/index.html">Extensions</a> ({len(exts)})</li>
<li><a href="profile/index.html">Profiles</a> ({len(P.names)}): RVI20, RVA20, RVA22, RVA23, RVB23 and their extensions</li>
<li><a href="specs.html">Specifications and documents</a>: every RISC-V document, every known extension, work in progress</li>
</ul>
<p class="meta">Data: riscv-unified-db <code>{commit[:12]}</code> · generated {today}</p>
"""
    write(a.out, "index.html", page(top, "Home", home, "RISC-V instructions, CSRs and extensions reference"))

    about = f"""
<h1>About</h1>
<p>This site is an unofficial, static reference for the RISC-V ISA. Every page is generated from the
<a href="https://github.com/riscv-software-src/riscv-unified-db">RISC-V Unified Database</a> (UDB),
commit <code>{commit}</code>. Operations are written in UDB's IDL (Instruction Definition Language).
Some UDB descriptions are templates meant to be resolved for one processor configuration. This site
shows the text for a processor implementing every extension, and keeps every other variant next to it,
labelled <em>With …</em>, <em>Without …</em> or <em>When …</em>; a value that depends on extensions lists
each alternative.
When this site and the ratified specifications disagree, the
<a href="https://github.com/riscv/riscv-isa-manual">RISC-V ISA manual</a> is authoritative.</p>
<h2>Licenses</h2>
<p>The ISA data comes from riscv-unified-db: © Qualcomm Technologies, Inc. and/or its subsidiaries,
and contributors to the RISC-V UnifiedDB, under the BSD-3-Clause-Clear license (a few files under CC-BY-4.0).
The generator and stylesheet are under the MIT license.</p>
<p>Search is powered by
<a href="https://pagefind.app">Pagefind</a>.</p>
"""
    write(a.out, "about.html", page(top, "About", about))
    for n in P.names:
        body, flt = profiles.profile_page(P, n, sub, SNAP, condition, strip_parens)
        write(a.out, f"profile/{n}.html", page(sub, n, body + filters(**flt), P.raw[n].get("long_name", n)))
    write(a.out, "profile/index.html", page(sub, "Profiles", profiles.index_page(P, sub, SNAP)
                                             + '<script src="../static/filter.js"></script>', "RISC-V profiles and their extensions"))
    if SNAP:
        body, _ = specs.page_body(SNAP, exts, top, PROFILES)
        write(a.out, "specs.html", page(top, "Specifications", body + '<script src="static/filter.js"></script>',
                                        "Every RISC-V specification document and every known extension"))
    exact = collections.defaultdict(list)
    for kind, items in (("inst", insts), ("csr", csrs), ("ext", exts)):
        for n, d in items.items():
            exact[n.lower()].append([n, f"{kind}/{n}.html", kind, d["long_name"].replace("`", "")])
    os.makedirs(os.path.join(a.out, "static"), exist_ok=True)
    with open(os.path.join(a.out, "static", "names.json"), "w", encoding="utf-8") as f:
        json.dump(exact, f, separators=(",", ":"))

    with tempfile.TemporaryDirectory() as tmp:
        prose.convert(tmp)
    flush(prose, names)
    print(f"generated {len(insts)} instructions, {len(csrs)} CSRs, {len(exts)} extensions -> {a.out}")


if __name__ == "__main__":
    main()
