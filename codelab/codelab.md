# Your first QNX resource manager on an Apple-Silicon Mac

summary: Build, run and watch a /dev/counter resource manager on QNX 8.0 - read(), write(), devctl() and ionotify() pulses - cross-compiled on an M-series Mac and run on a QEMU guest. Every command below was run for real; the output you see is pasted from that run.
id: qnx-first-resmgr-apple-silicon
categories: qnx, qnx-8.0, resource-manager, macos
status: Published
authors: The Telltale project
duration: 55 minutes

## Overview
Duration: 5:00

A **resource manager** is how a QNX driver or service shows up as a file: it attaches a name such as `/dev/counter` to the path space, and every `open()`, `read()`, `write()`, `devctl()` or `ionotify()` a client makes on that name arrives as a **message** that your code answers. There is no kernel driver to write; a resource manager is an ordinary user-space process, which is why a crash in one cannot take the kernel with it.

You will build `/dev/counter`, a counter that any process can use:

| Client call | What happens |
|---|---|
| `cat /dev/counter` | `read()`: prints the current value |
| `echo inc > /dev/counter` | `write()`: adds 1 (`reset` sets it to 0) |
| `counterctl add 40` | `devctl()`: a typed binary request/reply |
| `counterwatch` | `ionotify()`: sleeps until the value changes, then wakes on a **pulse**, with no polling |

### What you'll learn
- how the QNX resource-manager framework (`dispatch`, `resmgr`, `iofunc`) fits together
- how to cross-compile for QNX 8.0 **on an Apple-Silicon Mac**, where SDP 8.0 has no macOS host tools, with `qcc` in a Docker container
- how to run and poke your service on a QNX 8.0 guest under QEMU
- how `ionotify()` and pulses let a client wait on a device without polling, and the one registration call QNX 8.0 insists on that is easy to miss

### What you'll need
- an Apple-Silicon Mac with Docker Desktop (the container runs as `linux/amd64`, emulated)
- the **QNX Software Development Platform 8.0** installed on the Mac (non-commercial license; follow QNX's own guide, [How to use macOS to develop for QNX 8.0](https://devblog.qnx.com/how-to-use-macos-to-develop-for-qnx-8-0/)), at `~/qnx800` in this codelab
- the **QNX Everywhere Quick Start Target Image** for QEMU ([getting started](https://www.qnx.com/developers/docs/qnxeverywhere/com.qnx.doc.target_images/topic/qsti_qemu/getting_started.html)) and `qemu-system-x86_64` (`brew install qemu`)
- about 55 minutes, and basic C

### What this was tested on
Apple-Silicon Mac, Docker Desktop, QNX SDP 8.0 (`ntox86_64-gcc 12.2.0`), the Quick Start Target Image run as an **x86_64 guest under QEMU with TCG** (software emulation, so it is slow but needs no root, no KVM and no extra drivers). All code is in this repository's `codelab/` folder.

## Set up the host toolchain
Duration: 5:00

SDP 8.0's host tools are Linux-only, so on a Mac the compiler lives in a `linux/amd64` container. The image only holds a few helper packages; the SDP itself stays on your Mac and is mounted in at the same path. Build the image once from this repository's `docker/Dockerfile`:

```bash run=host-image
docker build -q --platform linux/amd64 -t telltale-qnx-host:local ../docker
docker image inspect telltale-qnx-host:local --format '{{.Os}}/{{.Architecture}}'
```

`env.sh` defines three small shell functions used for the rest of the codelab. `source` it in every terminal you use (it works in bash and zsh):

- `qnx <cmd>`: run `<cmd>` inside the container with the SDP environment loaded
- `tgt <cmd>`: run `<cmd>` on the QNX guest over ssh
- `tgtput <files>` / `tgtroot <script>`: copy files to the guest / run a script there as root

Check the cross-compiler answers:

```bash run=host-qcc
source env.sh
qnx ntox86_64-gcc --version | head -1
```

## Boot QNX 8.0 under QEMU
Duration: 5:00

Boot the Quick Start image with the launch script in this repository. It is the image's own `mkqnximage --run` command adapted for macOS (TCG instead of KVM, no GPU window, user-mode networking so ssh is forwarded to `127.0.0.1:2222`), with the reasons in the script header:

```bash
../scripts/run_qemu.sh &        # leave it running; the guest's serial console is /tmp/tt-qemu/serial.sock
```

In our run, from launching that script to the guest answering ssh took about 16 seconds. The default login is `qnxuser` (password `qnxuser`). Wait for ssh and ask the guest who it is:

```bash run=boot-wait
source env.sh
until tgt true 2>/dev/null; do sleep 2; done
tgt 'uname -a; id -un'
```

If you would rather not type the password on every `tgt` call, install an ssh key once and `export CODELAB_KEY=<private key path>` (the recorded runs below used a key).

## The contract: counter.h
Duration: 3:00

The header is the only thing the server and its clients share. `devctl()` commands are 32-bit numbers built by the `__DIOF` / `__DIOT` / `__DION` macros, which encode a class, a command number and the data's type and direction. `_DCMD_MISC` is the class QNX reserves for non-QNX drivers; the command numbers are ours.

{{file src/counter.h}}

## The server's state
Duration: 4:00

Resource-manager helpers like `iofunc_*` work on an `iofunc_attr_t`, the per-device attribute block. To keep our own data next to it, make our struct start with one. Two details matter:

1. `IOFUNC_ATTR_T` must be `#define`d **before** `<sys/iofunc.h>`, so the library's per-open (`ocb`) structure points at *our* struct.
2. `notify[3]` is the bookkeeping `iofunc_notify()` needs to remember which clients asked to be woken (input, output, out-of-band). We use it in the `ionotify()` step.

{{between src/counter.c "/* Our attribute" "static resmgr_connect_funcs_t"}}

## read() and write()
Duration: 8:00

Every handler gets the message (`msg`), the per-open state (`ocb`) and a context (`ctp`) for replying. Our `read()` formats the value as text. Two QNX idioms:

- **`ocb->offset`** is the file position. Returning the text once and then 0 bytes is what makes `cat` stop; without it `cat` would loop forever.
- The reply bytes go to the client with `resmgr_msgwrite()`, and the handler returns `_RESMGR_NPARTS(0)` with the byte count set by `_IO_SET_READ_NBYTES`. (A stack buffer cannot be returned through the IOV, because the reply is sent after the handler returns.)

{{fn src/counter.c io_read}}

`write()` reads the client's bytes with `resmgr_msgread()` (the offset `sizeof msg->i` skips the message header) and understands two words. Anything else is `EINVAL`, which the shell reports for you:

{{fn src/counter.c io_write}}

`counter_changed()`, near the top of the file, sets `unread` and calls `iofunc_notify_trigger()`; ignore it until the `ionotify()` step.

## devctl()
Duration: 5:00

`devctl()` is a typed request/reply: the client sends a command plus a small struct, the server sends a struct back. Always give the standard devctls (`iofunc_devctl_default`) the first chance, then handle your own; the data lives right after the message header, at `_DEVCTL_DATA()`. The reply must be a `struct _io_devctl_reply` plus any returned bytes.

{{fn src/counter.c io_devctl}}

## main(): attach a name
Duration: 5:00

`main()` is where the framework gets wired together: create a dispatch handle, fill the connect/io function tables with the library defaults (`iofunc_func_init`), override the handlers you wrote, initialise the attribute (`S_IFCHR | 0666`: a character device anyone may open), and attach the name. After that it is the standard receive loop.

{{fn src/counter.c main}}

Two handlers we skipped are short: `io_close_ocb()` drops a closing client's armed notifications so a dead client cannot be woken, and `io_notify()` is the `ionotify()` entry, covered below.

## Build it for QNX and copy it over
Duration: 3:00

Three programs: the server (`counter`) and two clients (`counterctl`, `counterwatch`). `-Wall -Wextra -Werror` keeps the build honest. The `file` line proves the result is a QNX 8.0 binary (its interpreter is QNX's `ldqnx-64.so.2`), not a Mac or Linux one:

```bash run=build
source env.sh
mkdir -p out
for p in counter counterctl counterwatch; do
  qnx qcc -Vgcc_ntox86_64 -std=gnu11 -Wall -Wextra -Werror -o out/$p src/$p.c && echo "built out/$p"
done
qnx file out/counter | cut -d, -f1-5
```

Copy the binaries to the guest:

```bash run=deploy
source env.sh
tgtroot 'slay counter 2>/dev/null; true'      # stop an older copy, if any
tgt 'rm -rf ~/codelab'
tgtput out/counter out/counterctl out/counterwatch
tgt 'ls -l ~/codelab'
```

## Run it
Duration: 5:00

A resource manager attaches a name in the **system path space**, which an ordinary user may not do. Try it as `qnxuser` first:

```bash run=notroot allow-fail
source env.sh
tgt 'cd ~/codelab && ./counter'
```

`Operation not permitted` is QNX 8.0's security model working as intended: registering `/dev/counter` needs the path-space ability, which `root` has. Start it as root, in the background:

```bash run=start
source env.sh
tgtroot 'slay counter 2>/dev/null
cd /data/home/qnxuser/codelab
nohup ./counter > counter.log 2>&1 < /dev/null &
sleep 1; cat counter.log'
tgt 'ls -l /dev/counter'
```

`/dev/counter` is now a file in the guest. Use it like one, with no special client library:

```bash run=try-rw
source env.sh
tgt 'cat /dev/counter; echo inc > /dev/counter; echo inc > /dev/counter; cat /dev/counter'
```

Our `write()` rejects words it does not know; the shell prints the `EINVAL` it got back:

```bash run=try-bad
source env.sh
tgt 'echo bogus > /dev/counter; echo "exit status: $?"'
```

Now the binary interface, through `counterctl`, which calls `devctl()`:

```bash run=try-devctl
source env.sh
tgt '~/codelab/counterctl add 40; ~/codelab/counterctl get; ~/codelab/counterctl reset; cat /dev/counter'
```

The server is an ordinary process, one you can see, stop and restart like any other (as `qnxuser`, `pidin` does not list root's processes, so these commands run as root). Kill it and the name disappears from the path space; start it again and it is back with a fresh counter:

```bash run=inspect allow-fail
source env.sh
tgtroot 'pidin -f an | grep "[.]/counter"'
tgtroot 'slay counter'
tgt 'cat /dev/counter'
```

```bash run=restart
source env.sh
tgtroot 'cd /data/home/qnxuser/codelab
nohup ./counter > counter.log 2>&1 < /dev/null &
sleep 1; cat counter.log'
tgt 'cat /dev/counter'
```

## ionotify(): wait without polling
Duration: 10:00

A client that wants to know when the counter changes has two bad options: poll in a loop (wastes CPU, adds latency) or block in `read()` (cannot do anything else). QNX's answer is **`ionotify()`**: the client says "send me this pulse when `/dev/counter` has input", and goes to sleep in `MsgReceivePulse()`, where it can also receive messages from anyone else.

The server side has two parts. `io_notify()` tells the library which conditions are true *right now*; the library arms the client's event for the others. `counter_changed()` fires the armed events whenever the value changes:

{{fn src/counter.c io_notify}}

{{fn src/counter.c counter_changed}}

The client creates a channel, connects to it, and builds a pulse event. Then comes the QNX 8.0 detail the first version of this codelab got wrong: **the event must be registered against the file descriptor with `MsgRegisterEvent()` before it is handed to `ionotify()`**. Without it everything appears to work (`ionotify()` returns 0, the server logs the trigger) and the pulse silently never arrives. It is called out in the `ionotify()` reference page, and took a debugging session to find. `POLLARM` is "tell me what is ready now, and arm the pulse only if nothing is", so the loop drains first and sleeps second:

{{fn src/counterwatch.c main}}

Run the watcher for three changes. A background job changes the counter from a second terminal-worth of work (a `write`, then a `devctl`, then another `write`) while `counterwatch` sleeps in between:

```bash run=watch
source env.sh
tgt 'cat /dev/counter >/dev/null'            # mark the current value as seen
( sleep 2; tgt 'echo inc > /dev/counter'; sleep 1; tgt '~/codelab/counterctl add 5'; sleep 1; tgt 'echo inc > /dev/counter' ) &
tgt '~/codelab/counterwatch 3'
wait
```

Three changes, three wake-ups, no polling: the watcher was blocked in the kernel between them. That is the whole mechanism behind `select()` and `poll()` on QNX too; they are built on `ionotify()`.

## Pitfalls and what to try next
Duration: 2:00

Things that went wrong while writing this codelab, so they need not go wrong for you:

- **No pulse arrives.** You forgot `MsgRegisterEvent(&ev, fd)`, or you armed with an event whose connection (`coid`) does not belong to the process that calls `MsgReceivePulse()`.
- **`cat /dev/counter` never ends.** Your `read()` does not advance `ocb->offset` / return 0 at the end.
- **`resmgr_attach: Operation not permitted`.** Not root. Resource managers that must run as a normal user get the path-space ability from a security policy (`secpol`); see QNX's *Security policies* docs.
- **`devctl()` seems to fail with `-1`.** It does not: `devctl()` returns the `errno` value itself (0 on success).
- **Handler "returns" a stack buffer.** The reply is sent after your handler returns; use `resmgr_msgwrite()` or static data.

To go further: give each *open file* its own state with an extended `ocb`; run the receive loop from a thread pool (`thread_pool_create`) instead of one thread; give the server a security policy so it does not need to be root; or trigger from a timer so the counter is a heartbeat. The Telltale repository this codelab ships in uses the same building blocks (`resmgr_attach`, message passing, `ionotify`) for a safety monitor; its README has the details.
