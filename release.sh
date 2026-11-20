#!/usr/bin/env bash
# Cut a GitHub release of the index.
#
#   ./release.sh v1.5 notes.md
#
# The release carries one asset, MGT2-Master-Index.html -- a copy of the committed index.html
# under the stable name the README's "Download" link points at
# (releases/latest/download/MGT2-Master-Index.html). Rebuild and commit index.html first
# (python -m mgtindex.web); this script publishes what is on main, it does not build.
set -euo pipefail

REPO=dcsturman/mgt-index
ASSET=MGT2-Master-Index.html

if [[ $# -ne 2 ]]; then
  echo "usage: $0 <version, e.g. v1.5> <release-notes.md>" >&2
  exit 1
fi
version=$1 notes=$2

[[ -f $notes ]] || { echo "no such notes file: $notes" >&2; exit 1; }
notes=$(cd "$(dirname "$notes")" && pwd)/$(basename "$notes")   # absolute, before we cd away
cd "$(dirname "$0")"
[[ $version =~ ^v[0-9]+(\.[0-9]+)*$ ]] || { echo "version should look like v1.5" >&2; exit 1; }
[[ $(git branch --show-current) == main ]] || { echo "not on main" >&2; exit 1; }
git diff --quiet HEAD -- index.html || { echo "index.html has uncommitted changes" >&2; exit 1; }
git fetch -q origin main
[[ $(git rev-parse HEAD) == $(git rev-parse origin/main) ]] \
  || { echo "main is not in sync with origin/main -- push (or pull) first" >&2; exit 1; }

# gh names an asset after the file's basename ("path#label" only sets a display label), so
# stage a copy under the asset name.
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
cp index.html "$tmp/$ASSET"

gh release create "$version" "$tmp/$ASSET" -R "$REPO" --target main \
  --title "MGT2 Master Index $version" --notes-file "$notes"
