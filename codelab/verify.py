#!/usr/bin/env python3
"""Run every `bash run=NAME` block of codelab.md for real and record its output in transcripts/NAME.txt.

    python3 verify.py            run all blocks in order against the running QNX guest (see env.sh)
    python3 verify.py NAME ...   run only the named blocks

A block fails the run on a non-zero exit unless its info string also says `allow-fail`. Needs: Docker +
the SDP (env.sh), a booted guest on 127.0.0.1:2222, and CODELAB_KEY if you use an ssh key.
"""
import os, re, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
FENCE = re.compile(r"^```bash (?P<info>run=\S+.*)$")


def blocks(text):
    lines, i = text.splitlines(), 0
    while i < len(lines):
        m = FENCE.match(lines[i])
        if m:
            info = m.group("info").split()
            name = next(w[4:] for w in info if w.startswith("run="))
            body = []
            i += 1
            while not lines[i].startswith("```"):
                body.append(lines[i]); i += 1
            yield name, "allow-fail" in info, "\n".join(body) + "\n"
        i += 1


def main(argv):
    only = set(argv)
    os.makedirs(os.path.join(HERE, "transcripts"), exist_ok=True)
    text = open(os.path.join(HERE, "codelab.md")).read()
    failed = []
    for name, allow, body in blocks(text):
        if only and name not in only:
            continue
        t0 = time.time()
        r = subprocess.run(["bash", "-c", body], cwd=HERE, capture_output=True, text=True, timeout=900)
        out = (r.stdout + r.stderr).rstrip("\n") + "\n"
        open(os.path.join(HERE, "transcripts", name + ".txt"), "w").write(out)
        ok = r.returncode == 0 or allow
        print(f"{'ok  ' if ok else 'FAIL'} {name:12s} rc={r.returncode} {time.time()-t0:5.1f}s")
        if not ok:
            failed.append(name); print(out)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
