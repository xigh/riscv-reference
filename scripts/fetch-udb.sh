#!/bin/sh
# Fetch the pinned riscv-unified-db commit (ISA data only) into ./udb.
set -eu
cd "$(dirname "$0")/.."
COMMIT=$(cat UDB_COMMIT)
URL=https://github.com/riscv-software-src/riscv-unified-db.git
if [ ! -d udb/.git ]; then
  git init -q udb
  git -C udb remote add origin "$URL"
  git -C udb sparse-checkout set --no-cone 'spec/std/isa/**' 'LICENSE*' 'LICENSES/**' 'REUSE.toml'
fi
git -C udb fetch -q --depth 1 --filter=blob:none origin "$COMMIT"
git -C udb checkout -q --detach FETCH_HEAD
echo "udb at $(git -C udb rev-parse HEAD)"
