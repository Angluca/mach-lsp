#!/usr/bin/env bash
# the LSP protocol suite over stdio, against this leg's debug server
set -euo pipefail

target="${MACH_CI_TARGET:-}"
if [ -z "$target" ]; then
    case "$MACH_CI_LEG" in
        x86_64-linux)   target=linux-x86_64 ;;
        aarch64-linux)  target=linux-arm64 ;;
        x86_64-windows) target=windows-x86_64 ;;
        aarch64-darwin) target=darwin-aarch64 ;;
        x86_64-darwin)  target=darwin-x86_64 ;;
        *)
            echo "verify: no manifest target for leg $MACH_CI_LEG" >&2
            exit 1
            ;;
    esac
fi

# the .exe name first: git bash on windows also answers `-f mls` for mls.exe,
# and the suite needs the real file name
bin=""
for candidate in "out/$target/debug/bin/mls.exe" "out/$target/debug/bin/mls"; do
    if [ -f "$candidate" ]; then
        bin="$candidate"
        break
    fi
done
if [ -z "$bin" ]; then
    echo "verify: no debug server under out/$target/debug/bin" >&2
    ls -la "out/$target/debug/bin" >&2 || true
    exit 1
fi

python test/lsp_protocol.py "$bin"
