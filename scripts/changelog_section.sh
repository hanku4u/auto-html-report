#!/usr/bin/env bash
# Print one version's section of CHANGELOG.md, without its heading: the notes for a GitHub Release.
#
# Fails when the section is missing or empty, so the release workflow can refuse a final release with no
# notes before anything is published.
set -euo pipefail

version="${1:?usage: changelog_section.sh VERSION}"
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

notes="$(VERSION="$version" awk '
    /^## \[/ {
        in_section = (index($0, "## [" ENVIRON["VERSION"] "]") == 1)
        next
    }
    in_section { print }
' "$root/CHANGELOG.md")"

if [ -z "$(printf '%s' "$notes" | tr -d '[:space:]')" ]; then
    echo "CHANGELOG.md has no notes for $version" >&2
    exit 1
fi
printf '%s\n' "$notes"
