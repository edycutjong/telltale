#!/usr/bin/env bash
# Talk to the running QNX guest over ssh (forwarded by scripts/run_qemu.sh to 127.0.0.1:2222).
#   scripts/guest.sh run '<shell command>'        run as the image's default user
#   scripts/guest.sh sudo '<shell command>'       run as root through sudo
#   scripts/guest.sh put <local files...>         copy into /tmp/tt on the guest
#   scripts/guest.sh get '<remote glob>' <dir>    copy back to a local dir
#   scripts/guest.sh wait [seconds]               wait until ssh answers
#   scripts/guest.sh key                          print the one-time serial-console line that installs $TT_KEY.pub
# Env: TT_KEY (default ~/.ssh/telltale_qnx, an ed25519 key made by `key`), TT_GUEST_USER (default qnxuser),
#      TT_GUEST_PASS (the QSTI's documented default user password; used only for sudo -S on the guest).
set -euo pipefail
KEY="${TT_KEY:-$HOME/.ssh/telltale_qnx}"
GU="${TT_GUEST_USER:-qnxuser}"
GP="${TT_GUEST_PASS:-qnxuser}"
SSHO=(-i "$KEY" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ConnectTimeout=15)
cmd="${1:-}"; shift || true
case "$cmd" in
  run)  exec ssh "${SSHO[@]}" -p 2222 "$GU@127.0.0.1" "$*" ;;
  sudo) exec ssh "${SSHO[@]}" -p 2222 "$GU@127.0.0.1" "echo '$GP' | sudo -S -p '' sh -c '$*'" ;;
  put)  ssh "${SSHO[@]}" -p 2222 "$GU@127.0.0.1" 'mkdir -p /tmp/tt'
        exec scp -q "${SSHO[@]}" -P 2222 "$@" "$GU@127.0.0.1:/tmp/tt/" ;;
  get)  exec scp -q "${SSHO[@]}" -P 2222 "$GU@127.0.0.1:$1" "$2" ;;
  wait) for _ in $(seq 1 $(( ${1:-180} / 5 ))); do
          ssh "${SSHO[@]}" -o ConnectTimeout=4 -p 2222 "$GU@127.0.0.1" true 2>/dev/null && exit 0; sleep 5; done
        echo "guest.sh: ssh not up" >&2; exit 1 ;;
  key)  [ -f "$KEY" ] || ssh-keygen -q -t ed25519 -N '' -f "$KEY" -C telltale-qnx
        echo "log in on the serial console (socat - UNIX-CONNECT:\${TT_RUN:-/tmp/tt-qemu}/serial.sock) and paste:"
        echo "mkdir -p ~/.ssh && echo '$(cat "$KEY.pub")' >> ~/.ssh/authorized_keys && chmod 700 ~/.ssh && chmod 600 ~/.ssh/authorized_keys" ;;
  *) sed -n '2,11p' "$0"; exit 1 ;;
esac
