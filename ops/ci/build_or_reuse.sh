#!/usr/bin/env bash
# Build an image only when its inputs changed. Called by every workflow that builds a platform image (release.yml; the
# compute-*.yml workflows adopt it the same way, see ops/RELEASE.md "Automatic propagation"). Needs a logged-in docker.
#
#   build_or_reuse.sh check <image-repo> <inputs-hash>
#       Looks for <image-repo>:inputs-<hash> in the registry. Writes reused=true|false and, when found, digest=sha256:... to
#       $GITHUB_OUTPUT (stdout when unset). A registry error other than "not found" fails the step: never guess.
#   build_or_reuse.sh tag <image-repo> <inputs-hash> <digest> [extra-tag...]
#       After a build: gives the new digest the tag inputs-<hash> (and any extra tags, e.g. the release tag), so the next
#       release with the same inputs finds it. The digest is checked against the repo before it is tagged.
set -euo pipefail

out() { if [ -n "${GITHUB_OUTPUT:-}" ]; then echo "$1" >> "$GITHUB_OUTPUT"; else echo "$1"; fi; }
valid() { [[ "$2" =~ $1 ]] || { echo "::error::bad $3: $2" >&2; exit 2; }; }

cmd=${1:-}; repo=${2:-}; hash=${3:-}
valid '^ghcr\.io/[a-z0-9._/-]+$' "$repo" "image repo"
valid '^[0-9a-f]{64}$' "$hash" "inputs hash"

case "$cmd" in
  check)
    # --format prints the manifest digest of the tag; the exit code is the only signal about presence, so a failure is
    # treated as absent only when the registry says the tag does not exist.
    err=$(mktemp)
    if found=$(docker buildx imagetools inspect "$repo:inputs-$hash" --format '{{.Manifest.Digest}}' 2>"$err"); then
      valid '^sha256:[0-9a-f]{64}$' "$found" "digest for $repo:inputs-$hash"
      echo "reusing $repo@$found (inputs-$hash)" >&2
      out "reused=true"; out "digest=$found"
    elif grep -qiE 'not found|manifest unknown|name unknown' "$err"; then
      echo "no image for inputs-$hash: building" >&2
      out "reused=false"
    else
      cat "$err" >&2; echo "::error::cannot tell whether $repo:inputs-$hash exists" >&2; exit 1
    fi ;;
  tag)
    digest=${4:-}
    valid '^sha256:[0-9a-f]{64}$' "$digest" "digest"
    shift 4
    tags=(-t "$repo:inputs-$hash")
    for t in "$@"; do valid '^[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$' "$t" "tag"; tags+=(-t "$repo:$t"); done
    docker buildx imagetools create "${tags[@]}" "$repo@$digest" ;;
  *) echo "usage: $0 check <repo> <hash> | tag <repo> <hash> <digest> [tag...]" >&2; exit 2 ;;
esac
