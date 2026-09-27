#!/usr/bin/env bash
# Move this source into the canonical private Command Workers repository and
# prove nothing was lost on the way.
#
#   ./migrate_to_command_workers.sh git@github.com:sttmikeg-BJVI/command-workers.git
#
# It never rewrites or deletes anything in the destination: it pushes a working
# branch and stops. The receipt it writes compares the destination checkout
# against this tree file by file, so a file that was regenerated rather than
# moved shows up as changed.
set -euo pipefail

REMOTE="${1:?usage: migrate_to_command_workers.sh <git remote url> [branch]}"
BRANCH="${2:-command-workers/import}"
SOURCE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK="$(mktemp -d)"
PY="${PYTHON:-python3}"

echo "source:      $SOURCE"
echo "destination: $REMOTE ($BRANCH)"

git clone --quiet "$REMOTE" "$WORK/repo" 2>/dev/null || {
    mkdir -p "$WORK/repo"
    git -C "$WORK/repo" init --quiet
    git -C "$WORK/repo" remote add origin "$REMOTE"
}
git -C "$WORK/repo" checkout -q -B "$BRANCH"

# Copy the tracked source only; build and environment noise stays behind.
git -C "$SOURCE" ls-files -z | while IFS= read -r -d '' file; do
    mkdir -p "$WORK/repo/$(dirname "$file")"
    cp -p "$SOURCE/$file" "$WORK/repo/$file"
done

git -C "$WORK/repo" add -A
git -C "$WORK/repo" -c user.email=devin@bjvi -c user.name=devin \
    commit -q -m "Import Command Workers source (Conference, Jarvis, orchestration)" || true

"$PY" "$SOURCE/source_inventory.py" scan "$SOURCE" -o "$WORK/source.json" >/dev/null
"$PY" "$SOURCE/source_inventory.py" scan "$WORK/repo" -o "$WORK/destination.json" >/dev/null
set +e
"$PY" "$SOURCE/source_inventory.py" compare "$WORK/source.json" "$WORK/destination.json" \
    -o "$SOURCE/MIGRATION_RECEIPT.md"
INTEGRITY=$?
set -e

git -C "$WORK/repo" push -u origin "$BRANCH"

echo "receipt: $SOURCE/MIGRATION_RECEIPT.md"
exit "$INTEGRITY"
