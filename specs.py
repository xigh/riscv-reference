"""The "Specifications" page: every RISC-V document and every known extension.

Rendered from data/specs.json, a dated snapshot of RISC-V International's
lists (see scripts/fetch-specs.py), cross-referenced with the UDB extensions.
"""

import html
import re

E = html.escape
EXT_TOKEN = re.compile(r"\b(Z[a-z][A-Za-z0-9]*|S[a-z][A-Za-z0-9]*)\b")
ALIASES = {"Hypervisor": "H", "RV32I": "I", "RV64I": "I", "RV32E": "E", "RV64E": "E",
           "Sm1p11": "Sm", "Sm1p12": "Sm", "Sm1p13": "Sm", "Ss1p11": "S", "Ss1p12": "S", "Ss1p13": "S"}
# profile release -> title of the library document that defines it
PROFILE_DOC = {"RVA20": "RISC-V Profiles", "RVI20": "RISC-V Profiles", "RVA22": "RISC-V Profiles",
               "RVA23": "RVA23 Profile", "RVB23": "RVB23 Profile"}
VOLUME = {"unprivileged": "Unprivileged ISA", "privileged": "Privileged ISA"}


def _norm(s):
    return re.sub(r"[^a-z0-9 ]", " ", s.lower()).split()


def library_doc(spec_name, library):
    """The library document a ratified specification was merged into, if any."""
    name = spec_name.replace("Volume I:", "Unprivileged ISA").replace("Volume II:", "Privileged ISA")
    words = " ".join(_norm(name))
    best = None
    for doc in library:
        t = " ".join(_norm(doc["title"]))
        if t and t in words and (best is None or len(doc["title"]) > len(best["title"])):
            best = doc
    return best


def registry(snap, exts, profiles=None):
    """name -> info for every extension known to UDB or to RVI's lists."""
    reg = {}

    def entry(n):
        return reg.setdefault(n, {"name": n})

    for n, d in exts.items():
        e = entry(n)
        e["udb"] = True
        e["category"] = str(d.get("type", ""))
        vs = d.get("versions") or []
        if vs:
            e["udb_state"] = str(vs[-1].get("state", ""))
            urls = [v.get("url") for v in reversed(vs) if v.get("url")]
            urls += [r.get("url") for v in reversed(vs) for r in (v.get("repositories") or []) if r.get("url")]
            if urls:
                e["udb_url"] = urls[0]
    for spec in snap.get("ratified", []):
        doc = None if spec["url"] else library_doc(spec["spec"], snap["library"])
        for raw in spec["extensions"]:
            n = ALIASES.get(raw, raw)
            if re.fullmatch(r"RV[A-Z]\d+(p\d+)?", n):  # profiles (RVA20, RVI20...)
                continue
            e = entry(n)
            if "ratified" in e:  # the list is newest first: keep the latest specification
                continue
            e["ratified"] = spec["date"]
            e["spec"] = spec["spec"]
            e["spec_url"] = spec["url"] or (doc or {}).get("html", "")
    # U-mode is defined by the privileged manual, like S-mode
    if "U" in reg and "S" in reg and "spec" in reg["S"] and "spec" not in reg["U"]:
        for k in ("ratified", "spec", "spec_url"):
            reg["U"][k] = reg["S"][k]
    # extensions named by a profile and defined nowhere else
    for release, names in (profiles or {}).items():
        doc = next((d for d in snap["library"] if d["title"] == PROFILE_DOC.get(release)), None)
        for n in names:
            e = reg.get(n)
            if e is not None and "spec" not in e and "profile" not in e:
                e["profile"] = release
                e["profile_url"] = (doc or {}).get("html", "")
    for row in snap.get("status", []):
        if row["stage"].endswith("Ratifications"):
            continue
        for group in re.findall(r"\(([^)]*)\)", row["summary"]):
            for n in EXT_TOKEN.findall(group):
                e = entry(n)
                if "ratified" not in e:
                    e["stage"] = row["stage"]
                    e["dev"] = row
    return reg


def spec_line(e, snap, ctx):
    """'Specification:' line for an extension page."""
    if e.get("spec"):
        url = e.get("spec_url")
        if not url:
            doc = next((d for d in snap["library"] if d["title"] == VOLUME.get(e.get("category", ""))), None)
            url = doc["html"] if doc else ""
        name = f'<a href="{E(url)}">{E(e["spec"])}</a>' if url else E(e["spec"])
        return f'<p class="meta"><b>Specification:</b> {name} (ratified {E(e["ratified"])})</p>'
    if e.get("profile"):
        link = f'<a href="{E(e["profile_url"])}">{E(e["profile"])} profile</a>' if e.get("profile_url") else E(e["profile"])
        return f'<p class="meta"><b>Specification:</b> named in the {link}</p>'
    if e.get("udb_url") and not e.get("dev"):
        return f'<p class="meta"><b>Specification:</b> <a href="{E(e["udb_url"])}">document referenced by UDB</a></p>'
    if e.get("dev"):
        r = e["dev"]
        gh = f' · <a href="{E(r["github"])}">GitHub</a>' if r.get("github") else ""
        return (f'<p class="meta"><b>Specification:</b> <a href="{E(r["key_url"])}">{E(r["summary"])}</a> '
                f'({E(r["stage"])}){gh}</p>')
    return ""


def page_body(snap, exts, ctx, profiles=None):
    reg = registry(snap, exts, profiles)
    src = snap["sources"]
    b = ['<h1>Specifications and extensions</h1>',
         '<p>Every document published by RISC-V International, every extension known either to this '
         'reference or to RISC-V International, and the specifications still in progress.</p>',
         f'<p class="meta">Snapshot of {E(snap["fetched_at"][:10])} from '
         f'<a href="{E(src["library"])}">the ratified specifications library</a>, '
         f'<a href="{E(src["ratified"])}">the list of ratified extensions</a> and '
         f'<a href="{E(src["status"])}">the specification status board</a>.</p>',
         '<p class="toc"><a href="#documents">Documents</a> <a href="#extensions">All extensions</a> '
         '<a href="#ratified">Ratified specifications</a> <a href="#progress">In progress</a></p>']

    # 1. documents
    b.append(f'<h2 id="documents">Documents ({len(snap["library"])})</h2>')
    sections = []
    for d in snap["library"]:
        if d["section"] not in sections:
            sections.append(d["section"])
    for sec in sections:
        rows = []
        for d in (x for x in snap["library"] if x["section"] == sec):
            ln = " · ".join(f'<a href="{E(d[k])}">{lab}</a>' for k, lab in (("html", "HTML"), ("pdf", "PDF"), ("more", "Info")) if d.get(k))
            rows.append(f'<tr><td><b>{E(d["title"])}</b><br><span class="meta">{E(d["description"])}</span></td>'
                        f'<td class="nowrap">{E(d["version"])}</td><td class="nowrap">{E(d["date"])}</td><td class="nowrap">{ln}</td></tr>')
        b.append(f'<h3>{E(sec)}</h3><table class="list"><tr><th>Document</th><th>Version</th><th>Date</th><th>Links</th></tr>{"".join(rows)}</table>')

    # 2. every extension
    names = sorted(reg, key=str.lower)
    rows = []
    for n in names:
        e = reg[n]
        if e.get("ratified"):
            state = f'<span class="state state-ratified">ratified</span> {E(e["ratified"])}'
        elif e.get("stage"):
            state = f'<span class="state state-development">{E(e["stage"].lower())}</span>'
        else:
            state = f'<span class="state state-{E(e.get("udb_state", ""))}">{E(e.get("udb_state", "?"))}</span>'
        if e.get("spec"):
            url = e.get("spec_url") or next((d["html"] for d in snap["library"] if d["title"] == VOLUME.get(e.get("category", ""))), "")
            spec = f'<a href="{E(url)}">{E(e["spec"])}</a>' if url else E(e["spec"])
        elif e.get("profile"):
            spec = (f'named in the <a href="{E(e["profile_url"])}">{E(e["profile"])} profile</a>'
                    if e.get("profile_url") else f'named in the {E(e["profile"])} profile')
        elif e.get("dev"):
            r = e["dev"]
            spec = f'<a href="{E(r["key_url"])}">{E(r["summary"])}</a>'
            if r.get("github"):
                spec += f' · <a href="{E(r["github"])}">GitHub</a>'
        elif e.get("udb_url"):
            spec = f'<a href="{E(e["udb_url"])}">document referenced by UDB</a>'
        else:
            spec = '<span class="meta">not listed by RISC-V International</span>'
        ref = ctx.link("ext", n, "page") if e.get("udb") else '<span class="meta">not in this reference</span>'
        rows.append(f'<tr data-name="{E(n.lower())}"><td><code>{E(n)}</code></td><td>{state}</td><td>{spec}</td><td>{ref}</td></tr>')
    in_udb = sum(1 for e in reg.values() if e.get("udb"))
    b.append(f'<h2 id="extensions">All extensions ({len(names)})</h2>'
             f'<p class="meta">{in_udb} documented in this reference, {len(names) - in_udb} only listed by RISC-V International. '
             'Extensions without their own document are part of the ISA manual (Volume I or II).</p>'
             '<p><input id="ext-filter" class="filter" data-table="ext-table" type="search" placeholder="Filter extensions (name, state, document)…"></p>'
             f'<table class="list" id="ext-table"><tr><th>Extension</th><th>State</th><th>Specification</th><th>Reference</th></tr>{"".join(rows)}</table>')

    # 3. ratified specifications
    rows = []
    for s in snap["ratified"]:
        url = s["url"] or (library_doc(s["spec"], snap["library"]) or {}).get("html", "")
        name = f'<a href="{E(url)}">{E(s["spec"])}</a>' if url else E(s["spec"])
        ex = ", ".join(ctx.link("ext", ALIASES.get(x, x), x) for x in s["extensions"])
        rows.append(f'<tr><td class="nowrap">{E(s["date"])}</td><td>{name}</td><td>{ex}</td></tr>')
    b.append(f'<h2 id="ratified">Ratified specifications ({len(snap["ratified"])})</h2>'
             f'<table class="list"><tr><th>Ratified</th><th>Specification</th><th>Extensions</th></tr>{"".join(rows)}</table>')

    # 4. in progress
    stages = []
    for r in snap["status"]:
        if r["stage"] not in stages:
            stages.append(r["stage"])
    b.append('<h2 id="progress">Specifications in progress and recently ratified</h2>')
    for st in stages:
        rs = [r for r in snap["status"] if r["stage"] == st]
        rows = []
        for r in rs:
            links = [f'<a href="{E(r["key_url"])}">{E(r["key"])}</a>']
            if r.get("github"):
                links.append(f'<a href="{E(r["github"])}">GitHub</a>')
            if r.get("plan"):
                links.append(f'<a href="{E(r["plan"])}">plan</a>')
            date = r.get("ratified") or (r.get("updated", "")[:11])
            rows.append(f'<tr><td>{E(r["summary"])}</td><td>{E(r.get("isa", ""))}</td>'
                        f'<td class="nowrap">{E(date)}</td><td class="nowrap">{" · ".join(links)}</td></tr>')
        when = "Ratified" if st.endswith("Ratifications") else "Updated"
        b.append(f'<h3>{E(st)} ({len(rs)})</h3><table class="list"><tr><th>Specification</th><th>Type</th><th>{when}</th><th>Links</th></tr>{"".join(rows)}</table>')
    return "\n".join(b), reg
