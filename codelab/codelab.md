# Your first QNX resource manager on an Apple-Silicon Mac

summary: Build, run and watch a /dev/counter resource manager on QNX 8.0 - read(), write(), devctl() and ionotify() pulses - cross-compiled on an M-series Mac and run on a QEMU guest. Each command block, except the long-running QEMU boot command, is followed by the output recorded when it was run on that guest.
id: qnx-first-resmgr-apple-silicon
categories: qnx, qnx-8.0, resource-manager, macos
status: Published
authors: The Telltale project
duration: about {{total}} minutes (estimated per step)

## Overview
Duration: 5:00

A **resource manager** is how a QNX driver or service shows up as a file: it attaches a name such as `/dev/counter` to the path space, and every `open()`, `read()`, `write()`, `devctl()` or `ionotify()` a client makes on that name arrives as a **message** that your code answers. There is no kernel driver to write: a resource manager is an ordinary user-space process.

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
- how `ionotify()` and pulses let a client wait on a device without polling, and the registration call that is easy to miss (we show the failure, recorded)

### What you'll need
- an Apple-Silicon Mac with Docker Desktop (the container runs as `linux/amd64`, emulated)
- the **QNX Software Development Platform 8.0** installed on the Mac (non-commercial license; follow QNX's own guide, [How to use macOS to develop for QNX 8.0](https://devblog.qnx.com/how-to-use-macos-to-develop-for-qnx-8-0/)), at `~/qnx800`, with its license directory `~/.qnx`; Docker Desktop must be allowed to bind-mount both (and the folder you clone into)
- the **QNX Everywhere Quick Start Target Image** for QEMU ([getting started](https://www.qnx.com/developers/docs/qnxeverywhere/com.qnx.doc.target_images/topic/qsti_qemu/getting_started.html)): install the `com.qnx.qnx800.quickstart.qemu` package with QNX Software Center and run its `unpack_qemu_image.sh`; and `qemu-system-x86_64` (`brew install qemu`)
- this repository, cloned: `git clone https://github.com/edycutjong/telltale.git && cd telltale/codelab`. Every path below is relative to that `codelab/` folder
- about {{total}} minutes (the per-step times are estimates; the one-time image conversion in the boot step comes on top), and basic C

### What this was tested on
Written and run on 2026-11-16: Apple-Silicon Mac, Docker Desktop, QNX SDP 8.0 (`ntox86_64-gcc 12.2.0`), the Quick Start Target Image run as an **x86_64 guest under QEMU with TCG** (software emulation: slow, but it needs no root, no KVM and no extra drivers). Guest `uname`: `QNX qnxqemu 8.0.0 2026/06/05`. The complete source is in this folder's `src/`: you do not have to type the code that the steps below walk through. The commands were run in bash; `env.sh` also loads in zsh.

## Set up the host toolchain
Duration: 5:00

SDP 8.0's host tools are Linux-only, so on a Mac the compiler lives in a `linux/amd64` container. The image only holds a few helper packages; the SDP itself stays on your Mac and is mounted in at the same path. Build the image once from this repository's `docker/Dockerfile`:

```bash run=host-image
docker build -q --platform linux/amd64 -t telltale-qnx-host:local ../docker
docker image inspect telltale-qnx-host:local --format '{{.Os}}/{{.Architecture}}'
```

`env.sh` defines four small shell functions used for the rest of the codelab. `source` it in every terminal you use. Here is the image definition it runs, for reference:

{{file ../docker/Dockerfile}}

The functions:

- `qnx <cmd>`: run `<cmd>` inside the container with the SDP environment loaded
- `tgt <cmd>`: run `<cmd>` on the QNX guest over ssh
- `tgtput <files>` / `tgtroot <script>`: copy files to `~/codelab` on the guest (on the guest, `~` is `/data/home/qnxuser`) / run a script there as root

Check the cross-compiler answers:

```bash run=host-qcc
source env.sh
qnx ntox86_64-gcc --version | head -1
```

## Boot QNX 8.0 under QEMU
Duration: 6:00

Boot the Quick Start image with the launch script in this repository. It is the image's own `mkqnximage --run` command adapted for macOS (TCG instead of KVM, no GPU window, user-mode networking so ssh is forwarded to `127.0.0.1:2222`), with the reasons in the script header. The first time it runs it converts the image's 47 GiB raw disk to a qcow2 file (about 5 GB on disk; about a minute on an SSD) and checks the copy; after that it boots straight away. The command is long-running, so it is shown but not recorded; leave it running in its own terminal:

```bash
../scripts/run_qemu.sh          # the guest's serial console is the unix socket /tmp/tt-qemu/serial.sock
```

The image's default login is `qnxuser` (password `qnxuser`). You will run a few dozen ssh commands, so install an ssh key once: the loop below retries until `ssh-copy-id` succeeds (the guest needs a moment before its sshd answers); when it asks for the password, type `qnxuser`. `env.sh` picks the key up automatically in any terminal once `~/.ssh/qnx_codelab` exists:

```bash run=key
KEY=~/.ssh/qnx_codelab
[ -f $KEY ] || ssh-keygen -q -t ed25519 -N '' -f $KEY
until ssh-copy-id -i $KEY.pub -p 2222 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
      -o LogLevel=ERROR qnxuser@127.0.0.1; do sleep 3; done
export CODELAB_KEY=$KEY
```

Ask the guest who it is:

```bash run=boot-wait
source env.sh
tgt 'uname -a; id -un'
```

## The contract: counter.h
Duration: 3:00

The header is the only thing the server and its clients share. `devctl()` commands are 32-bit numbers built by the `__DIOF` / `__DIOT` / `__DION` macros, which encode a class, a command number, the size of the data and its direction. `_DCMD_MISC` is the class QNX recommends for non-QNX drivers; the command numbers are ours. The suffix says which way the data flows: `F` = from the device to the client, `T` = to the device from the client, `N` = no data.

{{file src/counter.h}}

## The server's state
Duration: 4:00

Resource-manager helpers like `iofunc_*` work on an `iofunc_attr_t`, the per-device attribute block. To keep our own data next to it, make our struct start with one. Two details matter:

1. `IOFUNC_ATTR_T` must be `#define`d **before** `<sys/iofunc.h>`, so the library's per-open (`ocb`) structure points at *our* struct.
2. The three static variables below are what `main()` fills in: the connect and I/O function tables and the one device's attribute. `notify[3]` is the bookkeeping `iofunc_notify()` needs to remember which clients asked to be woken (input, output, out-of-band). We use it in the `ionotify()` step.

{{between src/counter.c "/* Our attribute" "static void counter_changed"}}

## read() and write()
Duration: 8:00

Every handler gets the message (`msg`), the per-open state (`ocb`) and a context (`ctp`) for replying. Our `read()` formats the value as text. Two QNX idioms:

- **`ocb->offset`** is the file position. Returning the text once and then 0 bytes is what makes `cat` stop; without it `cat` would loop forever. It is a 64-bit value any client can set with `lseek()`, so the handler checks it at full width before using it as an index into the text.
- The reply bytes go to the client with `resmgr_msgwrite()`, and the handler returns `_RESMGR_NPARTS(0)` with the byte count set by `_IO_SET_READ_NBYTES`. (A stack buffer cannot be returned through the IOV, because the reply is sent after the handler returns.)

{{fn src/counter.c io_read}}

`write()` reads the client's bytes with `resmgr_msgread()` (the offset `sizeof msg->i` skips the message header) and understands two words. Anything else is `EINVAL`, which the shell reports for you. A write longer than the buffer, or one with a NUL byte inside, is refused rather than cut short, so `inc` followed by junk is not quietly accepted:

{{fn src/counter.c io_write}}

`counter_changed()`, near the top of the file, sets `unread` and calls `iofunc_notify_trigger()`; ignore it until the `ionotify()` step.

## devctl()
Duration: 5:00

`devctl()` is a typed request/reply: the client sends a command plus a small struct, the server sends a struct back. Always give the standard devctls (`iofunc_devctl_default`) the first chance, then handle your own; the data lives right after the message header, at `_DEVCTL_DATA()`. The reply is a `struct _io_devctl_reply` plus any returned bytes. Note the size checks: on `ADD` a client could send a struct of any size, so the server checks before it reads one; on `GET` the client says how big its buffer is, so the server checks that the reply fits before it writes one.

{{fn src/counter.c io_devctl}}

## main(): attach a name
Duration: 5:00

`main()` is where the framework gets wired together: create a dispatch handle, fill the connect/io function tables with the library defaults (`iofunc_func_init`), override the handlers you wrote, initialise the attribute (`S_IFCHR | 0666`: a character device anyone may open), and attach the name. After that it is the standard receive loop.

{{fn src/counter.c main}}

Two handlers we skipped are short: `io_close_ocb()` drops a closing client's armed notifications so a dead client cannot be woken, and `io_notify()` is the `ionotify()` entry, covered below.

## Build it for QNX and copy it over
Duration: 3:00

Three programs: the server (`counter`) and two clients (`counterctl`, `counterwatch`). `-Wall -Wextra -Werror` keeps the build honest. The `file` line shows the result is a QNX binary (its interpreter is QNX's `ldqnx-64.so.2`), not a Mac or Linux one:

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

`Operation not permitted` is QNX 8.0's security model working as intended: registering `/dev/counter` needs the path-space ability, which `root` has. Start it as root, in the background (`nohup` keeps it running after the ssh session ends):

```bash run=start
source env.sh
tgtroot 'slay counter 2>/dev/null
cd /data/home/qnxuser/codelab
nohup ./counter > counter.log 2>&1 < /dev/null &
sleep 1; cat counter.log'
tgt 'ls -l /dev/counter'
```

`/dev/counter` is now a file in the guest. Use it like one, with no special client library. A freshly started server's counter is 0:

```bash run=try-rw
source env.sh
tgt 'cat /dev/counter; echo inc > /dev/counter; echo inc > /dev/counter; cat /dev/counter'
```

Our `write()` rejects words it does not know; the shell prints the `EINVAL` it got back:

```bash run=try-bad
source env.sh
tgt 'echo bogus > /dev/counter; echo "exit status: $?"'
```

Now the binary interface, through `counterctl`, which calls `devctl()` (client side: open the device, then `devctl(fd, command, &data, size, NULL)`; the return value is 0 or an errno):

{{file src/counterctl.c}}

Run it:

```bash run=try-devctl
source env.sh
tgt '~/codelab/counterctl add 40; ~/codelab/counterctl get; ~/codelab/counterctl reset; cat /dev/counter'
```

The server is an ordinary process, one you can see, stop and restart like any other. `pidin` lists processes and `slay` kills one by name. Root's processes are not listed for `qnxuser`:

```bash run=pidin-user allow-fail
source env.sh
tgt 'pidin -f an | grep "[.]/counter" || echo "(no ./counter listed for qnxuser)"'
```

So run them as root. Kill the server and the name disappears from the path space (the `No such file` line is the expected result here):

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
Duration: 12:00

A client that wants to know when the counter changes has two bad options: poll in a loop (wastes CPU, adds latency) or block in `read()` (cannot do anything else). QNX's answer is **`ionotify()`**: the client says "send me this event when `/dev/counter` has input", and goes to sleep until it arrives.

Some vocabulary first. A **channel** is a mailbox a process creates to receive on (`ChannelCreate`; `_NTO_CHF_PRIVATE` keeps it private to that process, which is all a pulse-only client needs, while creating a public channel takes the `public_channel` ability in QNX 8.0's security model); a **connection** is a handle for sending to one (`ConnectAttach`; here the client connects to its own channel). A **pulse** is the smallest QNX message: a fixed-size, non-blocking packet with a small code and a value. A **`sigevent`** describes what to deliver; `SIGEV_PULSE_INIT` builds one that says "send this pulse to this connection". The server delivers it on the client's behalf when the condition becomes true, and the client picks it up with `MsgReceivePulse()` (or `MsgReceive()`, if the same thread must also serve messages from other clients).

The server side has two parts. `io_notify()` tells the library which conditions are true *right now*; for a `POLLARM` request, which is what the client below sends, the library arms the client's event only if none of the conditions the client asked about is true (see the `iofunc_notify()` reference page). `counter_changed()` fires the armed events whenever a `write()` or `devctl()` modifies the counter, even when the value ends up the same (for example `reset` at 0). Arming is one-shot: after an event fires, the client must arm again, which is why the client below loops. The `unread` flag is per device, not per open file: whoever reads the value clears it for every watcher.

{{fn src/counter.c io_notify}}

{{fn src/counter.c counter_changed}}

On the client, the part that is easy to miss: **the event must be registered against the file descriptor with `MsgRegisterEvent()` before it is handed to `ionotify()`**. The `ionotify()` reference page says so ("The application must register the event by calling MsgRegisterEvent() with the fd passed to ionotify()"). Leave it out and `ionotify()` still succeeds, but the pulse never arrives; the run below shows exactly that. `POLLARM` means "tell me what is ready now, and arm the pulse only if nothing is", so the loop drains first and sleeps second. The `lseek()` rewinds the open file so each `read()` returns the value again, from offset 0:

{{fn src/counterwatch.c main}}

First the broken variant: the same program built with `-DSKIP_REGISTER`, which leaves out the one `MsgRegisterEvent()` call. We change the counter, wait, and check whether the watcher woke up:

```bash run=watch-noreg
source env.sh
qnx qcc -Vgcc_ntox86_64 -std=gnu11 -Wall -Wextra -Werror -DSKIP_REGISTER -o out/counterwatch-noreg src/counterwatch.c
tgtput out/counterwatch-noreg
tgt 'cd ~/codelab; cat /dev/counter > /dev/null; ./counterwatch-noreg 1 > noreg.out 2>&1 < /dev/null &
sleep 2; echo inc > /dev/counter; sleep 4
cat noreg.out; echo "wake-ups seen: $(grep -c "value is now" noreg.out)"; pidin -p counterwatch-noreg'
tgt 'slay counterwatch-noreg' || true     # stop it
```

The client printed that it armed, the counter changed, and the output shows zero wake-ups after four seconds: no pulse arrived (the watcher is in `RECEIVE` state either way; the proof is the empty wake-up count, which the working run below contrasts). Now the real one, with the registration. While it sleeps we check what it is doing, then change the counter three times: a `write`, a `devctl`, another `write`:

```bash run=watch
source env.sh
tgt 'cat /dev/counter >/dev/null'            # mark the current value as seen
( sleep 1; tgt 'pidin -p counterwatch'; sleep 1; tgt 'echo inc > /dev/counter'; sleep 1; tgt '~/codelab/counterctl add 5'; sleep 1; tgt 'echo inc > /dev/counter' ) &
tgt '~/codelab/counterwatch 3'
wait
```

Three changes, three wake-ups, no polling: while it waits the watcher sits in `RECEIVE`, blocked in the kernel (the `pidin` line was taken before the first change). The timings use fixed sleeps: on a slow host the first value may print without a wait, and changes that arrive faster than the watcher re-arms coalesce into one wake-up that shows the latest value. `select()` and `poll()` on QNX are built on `ionotify()`, so the same server code serves them too.

## Pitfalls and what to try next
Duration: 2:00

Pitfalls:

- **No pulse arrives.** You forgot `MsgRegisterEvent(&ev, fd)` (see the recorded run above), or registered it against the wrong fd.
- **`cat /dev/counter` never ends.** Your `read()` does not advance `ocb->offset` / return 0 at the end.
- **`resmgr_attach: Operation not permitted`.** Not root. Resource managers that must run as a normal user get the path-space ability from a security policy (`secpol`); see QNX's *Security policies* docs.
- **`devctl()` seems to fail with `-1`.** It does not: `devctl()` returns the `errno` value itself (0 on success).
- **Handler "returns" a stack buffer.** The reply is sent after your handler returns; use `resmgr_msgwrite()` or static data.

To go further: give each *open file* its own state with an extended `ocb`; run the receive loop from a thread pool (`thread_pool_create`) instead of one thread; give the server a security policy so it does not need to be root; or trigger from a timer so the counter is a heartbeat. The Telltale repository this codelab ships in uses the same building blocks (`resmgr_attach`, message passing, `ionotify`) for an instrument-cluster monitor; its README has the details.
