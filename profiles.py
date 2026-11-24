"""Profile pages: families, releases, each profile with its resolved extension
list, and a matrix of extensions across profiles.

UDB profiles inherit from one another, in two ways:
- a whole profile: top-level ``$inherits: profile/X.yaml#`` (deep merge);
- an extension list: ``extensions: {$inherits: [...], $remove: [...], ...}``.
Both are resolved here; an unknown reference stops the build.
"""

import glob
import html
import os
import re

import yaml

E = html.escape
ORDER = ["RVI20U32", "RVI20U64", "RVA20U64", "RVA20S64", "RVA22U64", "RVA22S64",
         "RVB23U64", "RVB23S64", "RVA23U64", "RVA23S64", "RVB23p1S64", "RVA23p1S64"]
OPTIONAL_KINDS = {
    "localized": "Localized options (expected in some regions or markets)",
    "development": "Development options (to become mandatory in a later release)",
    "expansion": "Expansion options (for specific markets or uses)",
    "transitory": "Transitory options (kept for compatibility, may be dropped)",
    "": "Optional",
}
# what a reader compares each profile with (UDB's inheritance chain differs:
# it derives RVA23 from RVB23, for instance)
PREDECESSOR = {"RVI20U64": "RVI20U32", "RVA20U64": "RVI20U64", "RVA20S64": "RVA20U64",
               "RVA22U64": "RVA20U64", "RVA22S64": "RVA20S64", "RVB23U64": "RVA22U64",
               "RVB23S64": "RVA22S64", "RVA23U64": "RVA22U64", "RVA23S64": "RVA22S64",
               "RVB23p1S64": "RVB23S64", "RVA23p1S64": "RVA23S64"}
RELEASE_KEY = {"RVA20": "RISC-V Profiles", "RVI20": "RISC-V Profiles", "RVA22": "RISC-V Profiles",
               "RVA23": "RVA23", "RVB23": "RVB23", "RVA23p1": "RVA23.1", "RVB23p1": "RVB23.1"}
DOC = {"RVA20": "RISC-V Profiles", "RVI20": "RISC-V Profiles", "RVA22": "RISC-V Profiles",
       "RVA23": "RVA23 Profile", "RVB23": "RVB23 Profile", "RVA23p1": "RVA23 Profile", "RVB23p1": "RVB23 Profile"}


def deep_merge(base, over):
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


class Profiles:
    def __init__(self, isa):
        self.isa = isa
        self.raw = {}
        for path in glob.glob(os.path.join(isa, "profile", "*.yaml")):
            with open(path, encoding="utf-8") as f:
                d = yaml.safe_load(f)
            self.raw[d["name"]] = d
        self.releases = {}
        for path in glob.glob(os.path.join(isa, "profile_release", "*.yaml")):
            with open(path, encoding="utf-8") as f:
                d = yaml.safe_load(f)
            self.releases[d["name"]] = d
        self.families = {}
        for path in glob.glob(os.path.join(isa, "profile_family", "*.yaml")):
            with open(path, encoding="utf-8") as f:
                d = yaml.safe_load(f)
            self.families[d["name"]] = d
        self.cache = {}
        self.names = sorted(self.raw, key=lambda n: ORDER.index(n) if n in ORDER else 99)

    @staticmethod
    def ref_name(ref):
        m = re.fullmatch(r"profile/(\w+)\.yaml#(/extensions)?", ref)
        if not m:
            raise ValueError(f"unsupported profile reference: {ref}")
        return m.group(1)

    def resolve(self, name, stack=()):
        if name in self.cache:
            return self.cache[name]
        if name in stack or name not in self.raw:
            raise ValueError(f"bad profile inheritance: {name} {stack}")
        d = dict(self.raw[name])
        own = dict(d.get("extensions") or {})
        inherited = {}
        if "$inherits" in d:
            parent = self.resolve(self.ref_name(d.pop("$inherits")), stack + (name,))
            inherited.update(parent["extensions"])
            parent = {k: v for k, v in parent.items() if not k.startswith("_") and k != "extensions"}
            d = deep_merge(parent, d)
        inh = own.pop("$inherits", [])
        for ref in [inh] if isinstance(inh, str) else inh:
            inherited.update(self.resolve(self.ref_name(ref), stack + (name,))["extensions"])
        rem = own.pop("$remove", [])
        for r in [rem] if isinstance(rem, str) else rem:
            inherited.pop(r, None)
        merged = dict(inherited)
        merged.update(own)
        d["extensions"] = merged
        self.cache[name] = d
        return d

    def changes(self, name):
        """(predecessor, {ext: 'new'|'changed'}, [removed]) relative to PREDECESSOR."""
        pred = PREDECESSOR.get(name)
        cur = self.resolve(name)["extensions"]
        if not pred:
            return None, {k: "new" for k in cur}, []
        old = self.resolve(pred)["extensions"]
        diff = {}
        for k, v in cur.items():
            if k not in old:
                diff[k] = "new"
            elif presence(old[k]) != presence(v):
                diff[k] = "changed"
        return pred, diff, sorted((k for k in old if k not in cur), key=str.lower)

    def release_of(self, d):
        rel = d.get("release")
        if isinstance(rel, dict) and "$ref" in rel:
            return re.search(r"profile_release/(\w+)\.yaml", rel["$ref"]).group(1)
        return re.match(r"RV[A-Z]\d+(p\d+)?", d["name"]).group(0)


def presence(v):
    p = v.get("presence")
    if p == "mandatory":
        return "mandatory", ""
    if p == "optional":
        return "optional", ""
    if isinstance(p, dict) and "optional" in p:
        return "optional", str(p["optional"])
    raise ValueError(f"unsupported presence: {p!r}")


def ratification(rel, snap):
    """Date from RISC-V International's status board when it has one, else UDB's."""
    key = RELEASE_KEY.get(rel)
    for row in snap.get("status", []):
        if row.get("ratified") and row["summary"] == key:
            return row["ratified"], "RISC-V International"
    for spec in snap.get("ratified", []):
        if rel in spec["extensions"]:
            return spec["date"], "RISC-V International"
    return None, None


def doc_for(rel, snap):
    title = DOC.get(rel)
    return next((d for d in snap.get("library", []) if d["title"] == title), None)


def version(v):
    """UDB writes Ruby-style constraints: "~> 2.1" means 2.1 or later, same major."""
    v = str(v).strip()
    m = re.fullmatch(r"~>\s*([\d.]+)", v)
    if m:
        return f'<span title="{E(v)}: version {E(m.group(1))} or a later one with the same major number">≥ {E(m.group(1))}</span>'
    return E(v)


def ext_cell(n, ctx):
    return ctx.link("ext", n)


def profile_page(P, name, ctx, snap, condition, strip_parens):
    d = P.resolve(name)
    rel = P.release_of(d)
    ctx.prose.current = f"profile/{name}.yaml"
    b = [f'<h1>{E(d.get("marketing_name", name))} <span class="long">{E(d.get("long_name", ""))}</span></h1>']
    meta = [f"<b>Release:</b> {E(rel)}", f"<b>Mode:</b> {E(str(d.get('mode', '')))}",
            f"<b>Base:</b> RV{E(str(d.get('base', '')))}I"]
    date, src = ratification(rel, snap)
    if date:
        meta.append(f"<b>Ratified:</b> {E(date)}")
    doc = doc_for(rel, snap)
    if doc:
        meta.append(f'<b>Document:</b> <a href="{E(doc["html"])}">{E(doc["title"])}</a> (<a href="{E(doc["pdf"])}">PDF</a>)')
    b.append('<p class="meta">' + " · ".join(meta) + "</p>")
    if d.get("introduction"):
        b.append(ctx.prose(d["introduction"]))
    if d.get("requirements"):
        b.append(f'<p class="meta"><b>Requires:</b> {strip_parens(condition(d["requirements"], ctx))}</p>')
    exts = d["extensions"]
    groups = {}
    for n, v in exts.items():
        kind, sub = presence(v)
        groups.setdefault((kind, sub), []).append((n, v))

    pred, diff, removed = P.changes(name)

    def table(items):
        rows = []
        for n, v in sorted(items, key=lambda x: x[0].lower()):
            new = {"new": '<span class="state state-ratified">new</span>',
                   "changed": '<span class="state state-development">changed</span>'}.get(diff.get(n), "") if pred else ""
            note = ctx.prose(v["note"]) if v.get("note") else ""
            rows.append(f'<tr><td><code>{ext_cell(n, ctx)}</code> {new}</td><td class="nowrap">{version(v.get("version", ""))}</td><td>{note}</td></tr>')
        return f'<table class="list"><tr><th>Extension</th><th>Version</th><th>Note</th></tr>{"".join(rows)}</table>'

    mand = groups.get(("mandatory", ""), [])
    b.append(f"<h2>Mandatory extensions ({len(mand)})</h2>" + table(mand))
    for sub, title in OPTIONAL_KINDS.items():
        items = groups.get(("optional", sub), [])
        if items:
            b.append(f"<h2>{E(title)} ({len(items)})</h2>" + table(items))
    unknown = [k for k in groups if k[0] == "optional" and k[1] not in OPTIONAL_KINDS]
    if unknown:
        raise ValueError(f"{name}: unknown optional kinds {unknown}")
    if pred:
        rm = ", ".join(f"<code>{ext_cell(n, ctx)}</code>" for n in removed) or "none"
        b.append(f'<p class="meta">Compared with {ctx.link("profile", pred)}: '
                 '<span class="state state-ratified">new</span> = not in it, '
                 '<span class="state state-development">changed</span> = mandatory there and optional here, or the reverse. '
                 f'Not carried over: {rm}.</p>')
    for key, title in (("recommendations", "Recommendations"), ("extra_notes", "Notes")):
        items = d.get(key) or []
        if items:
            parts = []
            for it in items:
                head = f"<p><b>{E(str(it.get('presence', '')).capitalize())}</b></p>" if it.get("presence") else ""
                parts.append(head + ctx.prose(it.get("text", "")))
            b.append(f"<h2>{title}</h2>" + "".join(parts))
    b.append(f'<p class="src">Source: <code>spec/std/isa/profile/{E(name)}.yaml</code></p>')
    return "\n".join(b), {"type": "Profile", "extension": list(exts), "base": f"RV{d.get('base', '')}"}


def index_page(P, ctx, snap):
    b = ['<h1>Profiles</h1>',
         '<p>A profile fixes a set of extensions that software can rely on: mandatory ones, which every '
         'compliant processor implements, and optional ones, grouped by intent. Profiles come in families '
         'and releases; within a release, a U-mode profile lists what user code may use and an S-mode '
         'profile adds what a supervisor (an OS kernel) may use.</p>']
    b.append("<h2>Families</h2>")
    for fam in ("RVI", "RVA", "RVB"):
        f = P.families.get(fam)
        if f:
            b.append(f"<h3>{E(fam)} <span class=\"long\">{E(f.get('long_name', ''))}</span></h3>"
                     + ctx.prose(f.get("description", "")))
    rows = []
    for rel in ("RVI20", "RVA20", "RVA22", "RVB23", "RVA23", "RVB23p1", "RVA23p1"):
        r = P.releases.get(rel)
        if not r:
            continue
        profs = [n for n in P.names if P.release_of(P.raw[n]) == rel]
        date, _ = ratification(rel, snap)
        doc = doc_for(rel, snap)
        dl = f'<a href="{E(doc["html"])}">{E(doc["title"])}</a>' if doc else ""
        rows.append(f'<tr><td><b>{E(r.get("marketing_name", rel))}</b></td><td>{E(str(r.get("state", "")))}</td>'
                    f'<td class="nowrap">{E(date or str(r.get("ratification_date", "")))}</td>'
                    f'<td>{", ".join(ctx.link("profile", n) for n in profs)}</td><td>{dl}</td></tr>')
    b.append('<h2>Releases</h2><table class="list"><tr><th>Release</th><th>State</th><th>Ratified</th>'
             f'<th>Profiles</th><th>Document</th></tr>{"".join(rows)}</table>'
             '<p class="meta">Ratification dates come from RISC-V International when it publishes them.</p>')

    # matrix
    cols = P.names
    allx = sorted({n for p in cols for n in P.resolve(p)["extensions"]}, key=str.lower)
    head = "".join(f'<th class="vert"><a href="profile/{E(p)}.html">{E(p)}</a></th>' for p in cols)
    rows = []
    for n in allx:
        cells = []
        for p in cols:
            v = P.resolve(p)["extensions"].get(n)
            if not v:
                cells.append("<td></td>")
                continue
            kind, sub = presence(v)
            if kind == "mandatory":
                cells.append('<td class="m" title="mandatory">M</td>')
            else:
                cells.append(f'<td class="o" title="optional {E(sub)}">{E((sub or "o")[0])}</td>')
        rows.append(f'<tr data-name="{E(n.lower())}"><td><code>{ctx.link("ext", n)}</code></td>{"".join(cells)}</tr>')
    b.append(f'<h2 id="matrix">Extensions across profiles ({len(allx)})</h2>'
             '<p class="meta"><b>M</b> mandatory · optional: <b>l</b> localized, <b>d</b> development, '
             '<b>e</b> expansion, <b>t</b> transitory, <b>o</b> other.</p>'
             '<p><input class="filter" data-table="matrix-table" type="search" placeholder="Filter extensions…"></p>'
             f'<div class="matrix-wrap"><table class="matrix" id="matrix-table"><tr><th>Extension</th>{head}</tr>{"".join(rows)}</table></div>')
    return "\n".join(b)
