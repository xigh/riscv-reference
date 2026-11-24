#!/usr/bin/env python3
"""Snapshot RISC-V International's specification lists into data/specs.json.

Sources (all public):
  - https://docs.riscv.org/reference/home/index.html  ratified specifications library
  - Confluence page 16154732 "Ratified ISA Extensions"
  - Confluence page 16154861 "RISC-V Specs Under Development" (status board)

The snapshot is committed, so the site build never depends on these sites.
Run it again to refresh, then review the diff. Python standard library only.
"""

import datetime
import html
import json
import os
import re
import sys
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "data", "specs.json")
LIBRARY = "https://docs.riscv.org/reference/home/index.html"
CONFLUENCE = "https://lf-riscv.atlassian.net/wiki"
RATIFIED_PAGE = "16154732"
STATUS_PAGE = "16154861"
UA = {"User-Agent": "riscv-reference spec snapshot (+https://github.com/xigh/riscv-reference)"}


def get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8")


def text(fragment):
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def links(fragment, base):
    return [urllib.parse.urljoin(base, html.unescape(h)) for h in re.findall(r'href="([^"]+)"', fragment)]


def cells(row):
    return re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S)


def tables(body):
    """Yield (preceding heading, header list, rows) for each table."""
    heading = ""
    pos = 0
    for m in re.finditer(r"<h[1-6][^>]*>(.*?)</h[1-6]>|<table.*?</table>", body, re.S):
        if m.group(0).startswith("<table"):
            rows = re.findall(r"<tr[^>]*>(.*?)</tr>", m.group(0), re.S)
            if rows:
                yield heading, [text(c) for c in cells(rows[0])], [cells(r) for r in rows[1:]]
        else:
            heading = text(m.group(1))
        pos = m.end()


def library():
    page = get(LIBRARY)
    out = []
    for sec in re.finditer(r'<details class="section-cards-toggle"[^>]*>\s*<summary>(.*?)</summary>(.*?)</details>', page, re.S):
        section = text(sec.group(1))
        for card in re.findall(r'<div class="card shadow-tl.*?<a href="[^"]*" class="card-more-link"[^>]*>More</a></div>', sec.group(2), re.S):
            title = text(re.search(r"<h5[^>]*>(.*?)</h5>", card, re.S).group(1))
            meta_html = re.search(r'class="card-meta"[^>]*>(.*?)</div></div>', card, re.S).group(1) + "</div>"
            meta = [text(x) for x in re.findall(r"<div>(.*?)</div>", meta_html, re.S)]
            version = next((m.replace("Version:", "").strip() for m in meta if m.startswith("Version:")), "")
            date = next((m for m in meta if not m.startswith("Version:")), "")
            body = re.search(r'class="card-body">(.*?)<div class="card-footer">', card, re.S)
            entry = {"section": section, "title": title, "version": version, "date": date,
                     "description": text(body.group(1)) if body else ""}
            for label in ("HTML", "PDF", "More"):
                m = re.search(r'<a href="([^"]+)"[^>]*>' + label + "</a>", card)
                if m:
                    entry[label.lower()] = urllib.parse.urljoin(LIBRARY, html.unescape(m.group(1)))
            out.append(entry)
    if not out:
        sys.exit("library: no card found, the page layout changed")
    return out


def confluence(page_id):
    data = json.loads(get(f"{CONFLUENCE}/rest/api/content/{page_id}?expand=body.view,version"))
    return data["body"]["view"]["value"], data["version"]["when"], data["title"]


def ratified():
    body, when, title = confluence(RATIFIED_PAGE)
    out = []
    for _, header, rows in tables(body):
        if header[:2] != ["Specification name", "Ratification date"]:
            continue
        for c in rows:
            if len(c) < 3:
                continue
            exts = [e.strip() for e in re.split(r"[,/]", text(c[2])) if e.strip()]
            out.append({"spec": text(c[0]), "url": (links(c[0], CONFLUENCE) or [""])[0],
                        "date": text(c[1]), "extensions": exts})
    if not out:
        sys.exit("ratified: no table found")
    return out, when


def status():
    """Rows of the status board. Empty cells are sometimes omitted, so fields
    are recognised by their content rather than by column position."""
    body, when, title = confluence(STATUS_PAGE)
    out = []
    for heading, header, rows in tables(body):
        stage = re.sub(r"\s*\d+\s+Issues?$", "", heading)
        for c in rows:
            vals = [text(x) for x in c]
            k = next((i for i, v in enumerate(vals) if re.fullmatch(r"RVS-\d+", v)), None)
            if k is None:
                continue
            row = {"stage": stage, "key": vals[k], "key_url": (links(c[k], CONFLUENCE) or [""])[0]}
            rest = [(v, c[i]) for i, v in enumerate(vals) if i != k]
            # the summary is the first free-text cell after the key
            for i, v in enumerate(vals):
                if i > k and v and not re.fullmatch(r"(Yes|No|ISA|NON-ISA)", v) and not v.startswith("http"):
                    row["summary"] = v
                    break
            for v, cell in rest:
                if v in ("ISA", "NON-ISA"):
                    row["isa"] = v
                elif v.startswith("https://github.com"):
                    row["github"] = v
                elif v.startswith("http") and "atlassian" in v:
                    row["plan"] = v
                elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
                    row["ratified"] = v
                elif re.fullmatch(r"\d{2} \w{3} \d{4}, \d{2}:\d{2}", v):
                    row["updated"] = v
                elif v.startswith("Specification "):
                    row["status"] = v
            if not row.get("summary"):
                sys.exit(f"status: no summary for {row['key']}")
            out.append(row)
    if not out:
        sys.exit("status: no table found")
    return out, when


def main():
    lib = library()
    rat, rat_when = ratified()
    st, st_when = status()
    snap = {
        "fetched_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "sources": {
            "library": LIBRARY,
            "ratified": f"https://lf-riscv.atlassian.net/wiki/spaces/HOME/pages/{RATIFIED_PAGE}",
            "ratified_updated": rat_when,
            "status": f"https://lf-riscv.atlassian.net/wiki/spaces/HOME/pages/{STATUS_PAGE}",
            "status_updated": st_when,
        },
        "library": lib,
        "ratified": rat,
        "status": st,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(snap, f, indent=1, ensure_ascii=False)
        f.write("\n")
    print(f"library {len(lib)} documents, ratified {len(rat)} specifications, status {len(st)} entries -> {os.path.relpath(OUT)}")


if __name__ == "__main__":
    main()
