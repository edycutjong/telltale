#!/usr/bin/env bash
# Run a command with the QNX SDP 8.0 environment inside the linux/amd64 host-tools container.
#   scripts/qcc.sh 'qcc -Vgcc_ntox86_64 -o out/hello spike/g2_hello.c'
# Env: QNX_SDP_HOST (default $HOME/qnx800), QNX_LIC_DIR (default $HOME/.qnx, the SDP license),
#      QNX_IMAGE (default telltale-qnx-host:local; build it with: docker build --platform linux/amd64 -t telltale-qnx-host:local docker)
# The SDP, the license dir and the repo are mounted at their own absolute paths, so qnxsdp-env.sh paths and
# compiler diagnostics match the host.
set -euo pipefail
SDP="${QNX_SDP_HOST:-$HOME/qnx800}"
LIC="${QNX_LIC_DIR:-$HOME/.qnx}"
IMG="${QNX_IMAGE:-telltale-qnx-host:local}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
[ -f "$SDP/qnxsdp-env.sh" ] || { echo "qcc.sh: no QNX SDP at $SDP (set QNX_SDP_HOST)" >&2; exit 1; }
exec docker run --rm --platform linux/amd64 --user "$(id -u):$(id -g)" -e HOME="$HOME" \
  -v "$SDP:$SDP" -v "$LIC:$LIC" -v "$REPO:$REPO" -w "$REPO" "$IMG" \
  bash -c "source '$SDP/qnxsdp-env.sh' >/dev/null && $*"
