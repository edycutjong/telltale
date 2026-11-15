#!/usr/bin/env python3
"""Drive the QNX guest's serial console (the unix socket opened by scripts/run_qemu.sh).

    tools/qnx_serial.py login USER PASS          log in on the console
    tools/qnx_serial.py send 'LINE' [WAIT_S]     send one line, print what the console printed back
    tools/qnx_serial.py install-key PUBKEY_FILE  log in with $TT_GUEST_USER/$TT_GUEST_PASS (defaults: the
                                                 QSTI's documented qnxuser/qnxuser) and append the key to
                                                 ~/.ssh/authorized_keys, so scripts/guest.sh can use ssh

Output is read from the console log file (TT_RUN/serial.log), not the socket, so nothing is lost.
"""
import os
import socket
import sys
import time

RUN = os.environ.get("TT_RUN", "/tmp/tt-qemu")
SOCK = os.path.join(RUN, "serial.sock")
LOG = os.path.join(RUN, "serial.log")


def send(line: str, wait: float = 2.0) -> str:
    start = os.path.getsize(LOG) if os.path.exists(LOG) else 0
    with socket.socket(socket.AF_UNIX) as s:
        s.connect(SOCK)
        s.sendall((line + "\r").encode())
        time.sleep(wait)
    with open(LOG, "rb") as f:
        f.seek(start)
        return f.read().decode("utf-8", "replace").replace("\r", "")


def login(user: str, password: str) -> bool:
    lines = send("", 2).strip().splitlines()
    if lines and lines[-1].rstrip().endswith(("$", "#")):
        return True  # already at a shell prompt
    send(user, 2)
    out = send(password, 5)
    return "$ " in out or "# " in out


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 1
    if argv[1] == "send":
        print(send(argv[2], float(argv[3]) if len(argv) > 3 else 2.0))
        return 0
    if argv[1] == "login":
        return 0 if login(argv[2], argv[3]) else 2
    if argv[1] == "install-key":
        user = os.environ.get("TT_GUEST_USER", "qnxuser")
        if not login(user, os.environ.get("TT_GUEST_PASS", "qnxuser")):
            print("login failed", file=sys.stderr)
            return 2
        key = open(argv[2]).read().strip()
        if not key.startswith("ssh-"):
            print("not a public key", file=sys.stderr)
            return 2
        out = send(
            f"mkdir -p ~/.ssh && grep -qF '{key}' ~/.ssh/authorized_keys 2>/dev/null"
            f" || echo '{key}' >> ~/.ssh/authorized_keys; chmod 700 ~/.ssh; chmod 600 ~/.ssh/authorized_keys; echo TT-KEY-OK",
            3,
        )
        return 0 if "TT-KEY-OK" in out.split("echo TT-KEY-OK")[-1] else 2
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
