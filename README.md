# RISC-V ISA Reference

A static, searchable reference for the RISC-V instruction set: one page per instruction (encoding diagram, assembly syntax, description, formal operation), per control and status register (address, fields, reset values) and per extension (what it defines, versions, ratification state).

Everything is generated from the [RISC-V Unified Database](https://github.com/riscv-software-src/riscv-unified-db) (UDB). The site is plain HTML with relative links, so the same build works at a domain root, under `/<repo>/` on GitHub Pages, or in a sub-directory of another site. Search runs in the browser with [Pagefind](https://pagefind.app), with no server.

## Build

Requirements: Python 3.10+ with PyYAML, Node.js 20+, git.

```sh
pip install -r requirements.txt
./scripts/build.sh          # fetch UDB, generate build/, index it
python3 -m http.server -d build 8000
```

Then open <http://localhost:8000/>. Search does not work from `file://`: use a local server.

Search results can be narrowed with filters: page type (instruction, CSR, extension), extension, base (RV32/RV64), encoding size (16 or 32 bits), CSR privilege level, ratification state and category (privileged or unprivileged, taken from the defining extension). Typing an exact name puts that page first, and Enter goes straight to it.

`?q=term` on any page opens it with that search already run, e.g. `inst/add.html?q=fence`.

## How it works

- `scripts/fetch-udb.sh` checks out the UDB commit pinned in `UDB_COMMIT` (ISA data only, sparse checkout) into `udb/`.
- `build.py` reads `spec/std/isa/{inst,csr,ext}` and writes `build/`. Descriptions are AsciiDoc, converted in a single [Asciidoctor.js](https://asciidoctor.org) run (`adoc.mjs`).
- Some UDB descriptions are ERB templates meant for one processor configuration. `erb.py` shows the text for a processor implementing every extension and keeps every other variant next to it, labelled "With …", "Without …" or "When …"; a value that depends on extensions lists each alternative. An unknown template construct stops the build rather than leaking into the site.
- `npx pagefind --site build` adds the search index.
- `profile/` has one page per profile (RVI20, RVA20, RVA22, RVB23, RVA23 and the 23.1 releases) with its mandatory and optional extensions, what changed since the previous profile, and a matrix of extensions across all profiles. UDB's profile inheritance (`$inherits`, `$remove`) is resolved by `profiles.py`. Ratification dates come from RISC-V International's status board when it has them (UDB has some wrong ones).
- `specs.html` lists every document of the [RISC-V ratified specifications library](https://docs.riscv.org/), every extension known to UDB or to RISC-V International (with its state and its document), the ratified specifications and those in progress. It is rendered from `data/specs.json`, a dated snapshot made by `scripts/fetch-specs.py` from RISC-V International's public pages. The snapshot is committed so that the build never depends on those sites; rerun the script to refresh it and review the diff.

To move to a newer UDB, change `UDB_COMMIT` and rebuild.

## Deployment

`.github/workflows/pages.yml` builds and deploys to GitHub Pages on every push to `main` (Settings → Pages → Source: GitHub Actions).

## License

Generator code: MIT (see `LICENSE`). The ISA data comes from UDB and stays under its own licenses (BSD-3-Clause-Clear, a few files CC-BY-4.0); see `NOTICE.md`.

This is an unofficial reference. When it disagrees with the ratified specifications, the [RISC-V ISA manual](https://github.com/riscv/riscv-isa-manual) is authoritative.
