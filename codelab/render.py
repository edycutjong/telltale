#!/usr/bin/env python3
"""Render codelab.md -> README.md (GitHub-readable) and index.html (standalone page).

Directives in codelab.md:
  {{file PATH}}                       the whole file, verbatim
  {{fn PATH NAME}}                    one C function, verbatim
  {{between PATH "START" "END"}}      lines from the first line starting with START up to (not incl.) the
                                      first later line starting with END
and, after every ```bash run=NAME block, the output recorded by verify.py in transcripts/NAME.txt.
Code and output are never typed by hand: they are copied from the files that were compiled and run.
"""
import html, os, re, sys
import markdown

HERE = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.expanduser("~")


def read(rel):
    return open(os.path.join(HERE, rel)).read()


def fn_text(path, name):
    lines = read(path).splitlines()
    pat = re.compile(r"^[A-Za-z_][\w \*]*\b%s\(" % re.escape(name))
    for i, l in enumerate(lines):
        if pat.match(l) and not l.rstrip().endswith(";"):
            j = i
            while lines[j] != "}":
                j += 1
            return "\n".join(lines[i:j + 1])
    sys.exit(f"render: function {name} not found in {path}")


def between(path, start, end):
    lines = read(path).splitlines()
    i = next(k for k, l in enumerate(lines) if l.startswith(start))
    j = next(k for k in range(i + 1, len(lines)) if lines[k].startswith(end))
    return "\n".join(lines[i:j]).rstrip()


def directive(m):
    kind, rest = m.group(1), m.group(2)
    args = re.findall(r'"([^"]*)"|(\S+)', rest)
    args = [a or b for a, b in args]
    if kind == "file":
        body = read(args[0]).rstrip("\n")
    elif kind == "fn":
        body = fn_text(args[0], args[1])
    elif kind == "between":
        body = between(args[0], args[1], args[2])
    else:
        sys.exit(f"render: unknown directive {kind}")
    lang = "dockerfile" if args[0].endswith("Dockerfile") else "c"
    cm = "#" if lang == "dockerfile" else "//"
    return f"```{lang}\n{cm} {args[0]}\n{body}\n```"


def clean(out):
    out = out.replace(HOME, "~")
    return "\n".join(l.rstrip() for l in out.rstrip("\n").splitlines())


def expand(text):
    text = re.sub(r"\{\{(file|fn|between) ([^}]*)\}\}", directive, text)
    # paste recorded output under each run block
    def run_block(m):
        name = next(w[4:] for w in m.group(1).split() if w.startswith("run="))
        tp = os.path.join(HERE, "transcripts", name + ".txt")
        if not os.path.exists(tp):
            sys.exit(f"render: no transcript for run={name}; run verify.py first")
        shown = re.sub(r"^```bash .*$", "```bash", m.group(0).split("\n", 1)[0]) + "\n" + m.group(2) + "```"
        return shown + "\n\nOutput (recorded):\n\n```text\n" + clean(open(tp).read()) + "\n```"
    text = re.sub(r"^```bash (run=[^\n]*)\n(.*?)^```", run_block, text, flags=re.S | re.M)
    return text


CSS = """
:root{--bg:#0e0f11;--panel:#16181b;--ink:#e9e6df;--mute:#a09b90;--line:#2a2d31;--acc:#6fd08c;--warn:#e8b25a}
*{box-sizing:border-box}html{scroll-behavior:smooth}
body{margin:0;background:var(--bg);color:var(--ink);font:17px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
.wrap{display:grid;grid-template-columns:260px minmax(0,780px);gap:48px;max-width:1160px;margin:0 auto;padding:32px 24px 96px}
nav{position:sticky;top:24px;align-self:start;font-size:14px;max-height:calc(100vh - 48px);overflow:auto}
nav b{display:block;margin-bottom:10px;color:var(--mute);letter-spacing:.08em;text-transform:uppercase;font-size:12px}
nav a{display:block;color:var(--mute);text-decoration:none;padding:6px 10px;border-left:2px solid var(--line)}
nav a:hover{color:var(--ink);border-color:var(--acc)}
nav a span{float:right;font-variant-numeric:tabular-nums;color:#6d695f}
h1{font-size:2.1rem;line-height:1.2;margin:0 0 .5rem}
.meta{color:var(--mute);font-size:15px;margin-bottom:2rem}
section.step{border-top:1px solid var(--line);padding-top:1.4rem;margin-top:2rem}
section.step h2{margin:0 0 .2rem;font-size:1.45rem}
.dur{display:inline-block;font-size:12.5px;color:var(--acc);border:1px solid var(--line);border-radius:99px;padding:1px 10px;margin-bottom:.8rem}
h3{font-size:1.1rem;margin:1.4rem 0 .3rem}
a{color:var(--acc)}code{font:0.88em ui-monospace,SFMono-Regular,Menlo,monospace;background:var(--panel);padding:.1em .35em;border-radius:4px}
pre{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:14px 16px;overflow:auto;line-height:1.5}
pre code{background:none;padding:0;font-size:13.5px}
pre.out{border-left:3px solid var(--acc)}
.lbl{font-size:12px;color:var(--mute);margin:.9rem 0 -.5rem;letter-spacing:.06em;text-transform:uppercase}
table{border-collapse:collapse;margin:1rem 0;font-size:15px}td,th{border:1px solid var(--line);padding:6px 12px;text-align:left}th{background:var(--panel)}
@media(max-width:900px){.wrap{display:block}nav{display:none}}
"""


def to_html(md_text, title, meta):
    body = markdown.markdown(md_text, extensions=["fenced_code", "tables"])
    # outputs: <p>Output (recorded):</p><pre><code class="language-text"> -> labelled output block
    body = body.replace("<p>Output (recorded):</p>\n<pre><code class=\"language-text\">", "<div class=\"lbl\">Output (recorded)</div>\n<pre class=\"out\"><code>")
    # group h2 steps
    parts = re.split(r"(<h2>.*?</h2>)", body)
    pre, steps = parts[0], []
    for i in range(1, len(parts), 2):
        h, rest = parts[i], parts[i + 1]
        m = re.search(r"<p>Duration: (\d+:\d\d)</p>", rest)
        dur = m.group(1) if m else ""
        rest = rest.replace(m.group(0), "") if m else rest
        name = re.sub(r"<.*?>", "", h)
        sid = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        steps.append((sid, name, dur, f'<section class="step" id="{sid}">{h.replace("<h2>", "<h2>")}<span class="dur">{dur} min</span>{rest}</section>'))
    nav = "".join(f'<a href="#{sid}">{html.escape(n)}<span>{d}</span></a>' for sid, n, d, _ in steps)
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{html.escape(title)}</title><meta name="description" content="{html.escape(meta.get("summary", ""))}">'
            f"<style>{CSS}</style></head><body><div class=\"wrap\"><nav><b>Steps</b>{nav}</nav><main>{pre}"
            + "".join(s for *_, s in steps) + "</main></div></body></html>")


def main():
    src = read("codelab.md")
    mins = sum(int(a) * 1 + int(b) / 60 for a, b in re.findall(r"^Duration: (\d+):(\d\d)$", src, flags=re.M))
    src = src.replace("{{total}}", str(round(mins)))
    lines = src.splitlines()
    title = lines[0].lstrip("# ").strip()
    meta = {}
    k = 1
    while k < len(lines) and not lines[k].startswith("## "):
        m = re.match(r"^(summary|id|categories|status|authors|duration): (.*)$", lines[k])
        if m:
            meta[m.group(1)] = m.group(2)
        k += 1
    body = "\n".join(lines[k:])
    full = expand(body)
    head = (f"# {title}\n\n> {meta.get('summary','')}\n>\n> **Duration:** {meta.get('duration','')} · **QNX:** 8.0 · "
            f"**Host:** Apple-Silicon Mac · **Rendered from `codelab.md`: every code block is copied from the files that "
            f"were compiled, every output block from the run recorded in `transcripts/`.**\n\n")
    open(os.path.join(HERE, "README.md"), "w").write(head + re.sub(r"\nDuration: (\d+:\d\d)\n", r"\n_Duration: \1_\n", full) + "\n")
    meta_line = f'<p class="meta">{html.escape(meta.get("summary",""))}<br>Duration {meta.get("duration","")} · QNX 8.0 · Apple-Silicon Mac host</p>'
    open(os.path.join(HERE, "index.html"), "w").write(to_html(f"<h1>{html.escape(title)}</h1>\n{meta_line}\n" + full, title, meta))
    print("wrote README.md index.html")


if __name__ == "__main__":
    main()
