#!/usr/bin/env bash
# Boundary enforcement (proposal §7.3): lint, not convention.
#
#   1. `temporalio` never enters the library — consumers own retry semantics.
#   2. `httpx` never enters `corpus/` — the port is transport-free.
#   3. Directus vocabulary never leaves `corpus_directus/`.
#
# Run the same script over a consumer's src/, scripts/ and tools/ after
# migration: a non-zero exit means a fork is growing back.
set -uo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
status=0

fail() { echo "BOUNDARY VIOLATION: $1"; status=1; }

if grep -rn --include='*.py' '\btemporalio\b' "$root/corpus" "$root/corpus_directus" 2>/dev/null; then
  fail "temporalio imported inside the library"
fi

if grep -rn --include='*.py' '^\s*\(import\|from\)\s\+httpx' "$root/corpus" 2>/dev/null; then
  fail "httpx imported inside the abstract port (adapters only)"
fi

vendor='items/articles\|items/editions\|filter\[\|datePublished\|articleBody\|articleKicker\|articleSection\|editionDate\|editionPdf'
vendor="$vendor\|referenceHeadline\|articleFeaturedImage\|articlePositionCover\|articleEdition\|filename_download"
targets=("$root/corpus")
for extra in "$@"; do targets+=("$extra"); done
if grep -rn --include='*.py' "$vendor" "${targets[@]}" 2>/dev/null; then
  fail "Directus vocabulary outside corpus_directus/"
fi

if [ "$status" -eq 0 ]; then echo "boundaries clean"; fi
exit "$status"
