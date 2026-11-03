#!/bin/sh
# Rebuild the extension repository in docs/ (bump `version` in blender_manifest.toml first).
set -e
cd "$(dirname "$0")"
B="${BLENDER:-/Applications/Blender.app/Contents/MacOS/Blender}"
rm -f docs/*.zip
mkdir -p docs
"$B" --command extension build --source-dir . --output-dir docs
"$B" --command extension server-generate --repo-dir docs --html
echo "Done. Now: git add -A && git commit -m 'Release <version>' && git push"
