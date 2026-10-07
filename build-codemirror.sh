#!/bin/bash
# Rebuilds static/codemirror.js, the CodeMirror 6 bundle used by the student editor, from
# static/codemirror-entry.js. Requires Node.js (npm). The versions are pinned below.
#
# Usage: ./build-codemirror.sh

set -euo pipefail

PACKAGES=(
    @codemirror/commands@6.11.1
    @codemirror/lang-python@6.2.1
    @codemirror/language@6.12.4
    @codemirror/state@6.7.6
    @codemirror/view@6.43.13
    @lezer/highlight@1.2.5
    @lezer/lr@1.4.10
)
ESBUILD=esbuild@0.28.2
# Oldest browsers to support. Safari 14.1 (iOS 14.5, 2021) is the oldest esbuild can
# convert CodeMirror's syntax for.
TARGET=safari14.1,chrome80,firefox78

cd "$(dirname "$0")"
BUILD_DIR="$(mktemp -d)"
trap 'rm -rf "$BUILD_DIR"' EXIT

(cd "$BUILD_DIR" && npm init -y > /dev/null && npm install --silent "${PACKAGES[@]}" "$ESBUILD")
cp static/codemirror-entry.js "$BUILD_DIR/"

"$BUILD_DIR/node_modules/.bin/esbuild" "$BUILD_DIR/codemirror-entry.js" \
    --bundle --minify --format=iife --global-name=CM --target="$TARGET" \
    --legal-comments=none \
    --banner:js="/* CodeMirror 6 (MIT license, https://codemirror.net): ${PACKAGES[*]}. Built by build-codemirror.sh. */" \
    --outfile=static/codemirror.js

echo "Built static/codemirror.js ($(wc -c < static/codemirror.js) bytes)"
