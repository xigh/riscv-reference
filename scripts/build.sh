#!/bin/sh
# Full build: fetch the pinned UDB, generate the pages, index them for search.
set -eu
cd "$(dirname "$0")/.."
[ -d node_modules ] || npm ci
./scripts/fetch-udb.sh
python3 build.py
npx pagefind --site build
