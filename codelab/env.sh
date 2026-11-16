# Source me:  source env.sh      (bash or zsh)
# tgt <command...>  runs a command on the QNX guest that scripts/run_qemu.sh forwards to 127.0.0.1:2222.
# The QNX Everywhere QEMU image's default login is qnxuser (password: qnxuser). To avoid typing it on every
# call, install an ssh key once, then export CODELAB_KEY=<path to that private key>.
tgt() {
  local k=(); [ -n "${CODELAB_KEY:-}" ] && k=(-i "$CODELAB_KEY")
  ssh "${k[@]}" -p 2222 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
      -o LogLevel=ERROR -o ConnectTimeout=15 qnxuser@127.0.0.1 "$@"
}
# tgtput <local files...>  copies files to ~/codelab on the guest.
tgtput() {
  local k=(); [ -n "${CODELAB_KEY:-}" ] && k=(-i "$CODELAB_KEY")
  tgt 'mkdir -p ~/codelab' && scp -q "${k[@]}" -P 2222 -o StrictHostKeyChecking=no \
      -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR "$@" qnxuser@127.0.0.1:codelab/
}
# tgtroot <sh script>  runs a script as root on the guest (sudo; feeds the default password only if sudo asks).
tgtroot() {
  printf '%s\n%s\n' "${CODELAB_SUDO_PW:-qnxuser}" "$1" | tgt \
    'IFS= read -r P; if sudo -n true 2>/dev/null; then exec sudo sh -s; else { printf "%s\n" "$P"; cat; } | sudo -S -p "" sh -s; fi'
}

# qnx <command...>  runs a command inside the linux/amd64 host-tools container with the QNX SDP 8.0 environment
# loaded (SDP 8.0 has no macOS host tools). The SDP, its license dir and the current directory are mounted at
# their own absolute paths so compiler diagnostics match your Mac.
QNX_SDP="${QNX_SDP:-$HOME/qnx800}"; QNX_LIC="${QNX_LIC:-$HOME/.qnx}"; QNX_IMAGE="${QNX_IMAGE:-telltale-qnx-host:local}"
qnx() {
  docker run --rm --platform linux/amd64 --user "$(id -u):$(id -g)" -e HOME="$HOME" \
    -v "$QNX_SDP:$QNX_SDP" -v "$QNX_LIC:$QNX_LIC" -v "$PWD:$PWD" -w "$PWD" "$QNX_IMAGE" \
    bash -c "source '$QNX_SDP/qnxsdp-env.sh' >/dev/null && $*"
}
