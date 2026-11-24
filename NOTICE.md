# Third-party data and software

## RISC-V Unified Database (the ISA data)

The generated site is built from [riscv-unified-db](https://github.com/riscv-software-src/riscv-unified-db), fetched at build time at the commit pinned in `UDB_COMMIT`. This repository does not contain that data; the published site does.

- Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries, and 2024-2025 Contributors to the RISC-V UnifiedDB.
- Almost all files are licensed under **BSD-3-Clause-Clear**; a few are under **CC-BY-4.0**. Each source file carries its SPDX identifier, and the full texts are in the UDB repository (`LICENSES/`).

Every generated page links back to UDB and names its source file.

## RISC-V International specification lists

`data/specs.json` is a snapshot of public lists published by RISC-V International (the ratified specifications library at docs.riscv.org, the "Ratified ISA Extensions" page and the specification status board on its wiki): titles, versions, dates and links. The documents themselves are not copied; the site links to them.

## Pagefind

Search uses [Pagefind](https://pagefind.app) (MIT), installed from npm at build time.

## Asciidoctor.js

Descriptions are converted with [Asciidoctor.js](https://asciidoctor.org) (MIT), installed from npm at build time.
