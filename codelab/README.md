# Your first QNX resource manager on an Apple-Silicon Mac

> Build, run and watch a /dev/counter resource manager on QNX 8.0 - read(), write(), devctl() and ionotify() pulses - cross-compiled on an M-series Mac and run on a QEMU guest. Every command below was run for real; the output you see is pasted from that run.
>
> **Duration:** 55 minutes · **QNX:** 8.0 · **Host:** Apple-Silicon Mac · **Rendered from `codelab.md`: every code block is copied from the files that were compiled, every output block from the run recorded in `transcripts/`.**

## Overview
_Duration: 5:00_

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
_Duration: 5:00_

SDP 8.0's host tools are Linux-only, so on a Mac the compiler lives in a `linux/amd64` container. The image only holds a few helper packages; the SDP itself stays on your Mac and is mounted in at the same path. Build the image once from this repository's `docker/Dockerfile`:

```bash
docker build -q --platform linux/amd64 -t telltale-qnx-host:local ../docker
docker image inspect telltale-qnx-host:local --format '{{.Os}}/{{.Architecture}}'
```

Output (recorded):

```text
sha256:f10a86d0df8c031deea73859e05560432d80bbcf777c600cb86036d6563305a5
linux/amd64
```

`env.sh` defines three small shell functions used for the rest of the codelab. `source` it in every terminal you use (it works in bash and zsh):

- `qnx <cmd>`: run `<cmd>` inside the container with the SDP environment loaded
- `tgt <cmd>`: run `<cmd>` on the QNX guest over ssh
- `tgtput <files>` / `tgtroot <script>`: copy files to the guest / run a script there as root

Check the cross-compiler answers:

```bash
source env.sh
qnx ntox86_64-gcc --version | head -1
```

Output (recorded):

```text
ntox86_64-gcc (QNX8.0.0-ra7e8d3cd2255b7b0fd7ed75b5fd7082edd813c9d-1008) 12.2.0
```

## Boot QNX 8.0 under QEMU
_Duration: 5:00_

Boot the Quick Start image with the launch script in this repository. It is the image's own `mkqnximage --run` command adapted for macOS (TCG instead of KVM, no GPU window, user-mode networking so ssh is forwarded to `127.0.0.1:2222`), with the reasons in the script header:

```bash
../scripts/run_qemu.sh &        # leave it running; the guest's serial console is /tmp/tt-qemu/serial.sock
```

In our run, from launching that script to the guest answering ssh took about 16 seconds. The default login is `qnxuser` (password `qnxuser`). Wait for ssh and ask the guest who it is:

```bash
source env.sh
until tgt true 2>/dev/null; do sleep 2; done
tgt 'uname -a; id -un'
```

Output (recorded):

```text
QNX qnxqemu 8.0.0 2026/06/05-16:26:03EDT x86pc x86_64
qnxuser
```

If you would rather not type the password on every `tgt` call, install an ssh key once and `export CODELAB_KEY=<private key path>` (the recorded runs below used a key).

## The contract: counter.h
_Duration: 3:00_

The header is the only thing the server and its clients share. `devctl()` commands are 32-bit numbers built by the `__DIOF` / `__DIOT` / `__DION` macros, which encode a class, a command number and the data's type and direction. `_DCMD_MISC` is the class QNX reserves for non-QNX drivers; the command numbers are ours.

```c
// src/counter.h
/* counter.h - the contract between the /dev/counter resource manager and its clients. */
#ifndef COUNTER_H
#define COUNTER_H

#include <stdint.h>
#include <devctl.h>

#define COUNTER_DEVPATH "/dev/counter"

/* _DCMD_MISC is the devctl class QNX recommends for non-QNX drivers. The command numbers are ours. */
#define DCMD_COUNTER_GET   __DIOF(_DCMD_MISC, 0x40, uint64_t) /* device -> client: current value */
#define DCMD_COUNTER_ADD   __DIOT(_DCMD_MISC, 0x41, int32_t)  /* client -> device: add a delta   */
#define DCMD_COUNTER_RESET __DION(_DCMD_MISC, 0x42)           /* no data: set the value to 0     */

#endif
```

## The server's state
_Duration: 4:00_

Resource-manager helpers like `iofunc_*` work on an `iofunc_attr_t`, the per-device attribute block. To keep our own data next to it, make our struct start with one. Two details matter:

1. `IOFUNC_ATTR_T` must be `#define`d **before** `<sys/iofunc.h>`, so the library's per-open (`ocb`) structure points at *our* struct.
2. `notify[3]` is the bookkeeping `iofunc_notify()` needs to remember which clients asked to be woken (input, output, out-of-band). We use it in the `ionotify()` step.

```c
// src/counter.c
/* Our attribute structure embeds the standard one first, so every iofunc_* helper still works on it.
 * IOFUNC_ATTR_T must be defined BEFORE <sys/iofunc.h> so the library knows the real type. */
struct counter_attr;
#define IOFUNC_ATTR_T struct counter_attr
#include <sys/iofunc.h>
#include <sys/dispatch.h>

#include "counter.h"

struct counter_attr {
    iofunc_attr_t   base;       /* must be first */
    iofunc_notify_t notify[3];  /* input / output / out-of-band, for ionotify() */
    uint64_t        value;
    int             unread;     /* value changed since the last complete read */
};
```

## read() and write()
_Duration: 8:00_

Every handler gets the message (`msg`), the per-open state (`ocb`) and a context (`ctp`) for replying. Our `read()` formats the value as text. Two QNX idioms:

- **`ocb->offset`** is the file position. Returning the text once and then 0 bytes is what makes `cat` stop; without it `cat` would loop forever.
- The reply bytes go to the client with `resmgr_msgwrite()`, and the handler returns `_RESMGR_NPARTS(0)` with the byte count set by `_IO_SET_READ_NBYTES`. (A stack buffer cannot be returned through the IOV, because the reply is sent after the handler returns.)

```c
// src/counter.c
static int io_read(resmgr_context_t *ctp, io_read_t *msg, iofunc_ocb_t *ocb)
{
    int s = iofunc_read_verify(ctp, msg, ocb, NULL);
    if (s != EOK) return s;
    if ((msg->i.xtype & _IO_XTYPE_MASK) != _IO_XTYPE_NONE) return ENOSYS;

    char text[32];
    int len = snprintf(text, sizeof text, "%llu\n", (unsigned long long)ocb->attr->value);
    int off = (int)ocb->offset, n = 0;
    if (off < len) {
        n = len - off;
        if (n > (int)msg->i.nbytes) n = (int)msg->i.nbytes;
        resmgr_msgwrite(ctp, text + off, n, 0);       /* copy into the client's buffer */
        ocb->offset += n;
        if (off + n >= len) ocb->attr->unread = 0;    /* the client has seen the whole value */
    }
    _IO_SET_READ_NBYTES(ctp, n);
    if (n) ocb->attr->base.flags |= IOFUNC_ATTR_ATIME;
    return _RESMGR_NPARTS(0);
}
```

`write()` reads the client's bytes with `resmgr_msgread()` (the offset `sizeof msg->i` skips the message header) and understands two words. Anything else is `EINVAL`, which the shell reports for you:

```c
// src/counter.c
static int io_write(resmgr_context_t *ctp, io_write_t *msg, iofunc_ocb_t *ocb)
{
    int s = iofunc_write_verify(ctp, msg, ocb, NULL);
    if (s != EOK) return s;
    if ((msg->i.xtype & _IO_XTYPE_MASK) != _IO_XTYPE_NONE) return ENOSYS;

    char cmd[16] = {0};
    size_t n = msg->i.nbytes < sizeof cmd - 1 ? msg->i.nbytes : sizeof cmd - 1;
    if (resmgr_msgread(ctp, cmd, n, sizeof msg->i) != (int)n) return EFAULT;
    while (n && (cmd[n - 1] == '\n' || cmd[n - 1] == ' ')) cmd[--n] = 0;

    if (!strcmp(cmd, "inc"))        ocb->attr->value++;
    else if (!strcmp(cmd, "reset")) ocb->attr->value = 0;
    else return EINVAL;
    counter_changed(ocb->attr);

    _IO_SET_WRITE_NBYTES(ctp, msg->i.nbytes);
    return _RESMGR_NPARTS(0);
}
```

`counter_changed()`, near the top of the file, sets `unread` and calls `iofunc_notify_trigger()`; ignore it until the `ionotify()` step.

## devctl()
_Duration: 5:00_

`devctl()` is a typed request/reply: the client sends a command plus a small struct, the server sends a struct back. Always give the standard devctls (`iofunc_devctl_default`) the first chance, then handle your own; the data lives right after the message header, at `_DEVCTL_DATA()`. The reply must be a `struct _io_devctl_reply` plus any returned bytes.

```c
// src/counter.c
static int io_devctl(resmgr_context_t *ctp, io_devctl_t *msg, iofunc_ocb_t *ocb)
{
    int s = iofunc_devctl_default(ctp, msg, ocb);     /* the standard devctls first */
    if (s != _RESMGR_DEFAULT) return s;

    struct counter_attr *a = ocb->attr;
    int nbytes = 0;
    switch (msg->i.dcmd) {
    case DCMD_COUNTER_GET:
        *(uint64_t *)_DEVCTL_DATA(msg->o) = a->value;
        nbytes = sizeof(uint64_t);
        break;
    case DCMD_COUNTER_ADD:
        a->value += (uint64_t)(int64_t)*(int32_t *)_DEVCTL_DATA(msg->i);
        counter_changed(a);
        break;
    case DCMD_COUNTER_RESET:
        a->value = 0;
        counter_changed(a);
        break;
    default:
        return ENOSYS;
    }
    memset(&msg->o, 0, sizeof msg->o);
    msg->o.nbytes = nbytes;
    return _RESMGR_PTR(ctp, &msg->o, sizeof msg->o + nbytes);
}
```

## main(): attach a name
_Duration: 5:00_

`main()` is where the framework gets wired together: create a dispatch handle, fill the connect/io function tables with the library defaults (`iofunc_func_init`), override the handlers you wrote, initialise the attribute (`S_IFCHR | 0666`: a character device anyone may open), and attach the name. After that it is the standard receive loop.

```c
// src/counter.c
int main(void)
{
    dispatch_t *dpp = dispatch_create();
    if (!dpp) { perror("dispatch_create"); return EXIT_FAILURE; }

    iofunc_func_init(_RESMGR_CONNECT_NFUNCS, &connect_funcs, _RESMGR_IO_NFUNCS, &io_funcs);
    io_funcs.read      = io_read;
    io_funcs.write     = io_write;
    io_funcs.devctl    = io_devctl;
    io_funcs.notify    = io_notify;
    io_funcs.close_ocb = io_close_ocb;

    iofunc_attr_init(&attr.base, S_IFCHR | 0666, NULL, NULL);
    IOFUNC_NOTIFY_INIT(attr.notify);

    resmgr_attr_t rattr;
    memset(&rattr, 0, sizeof rattr);
    rattr.nparts_max   = 1;
    rattr.msg_max_size = 2048;

    if (resmgr_attach(dpp, &rattr, COUNTER_DEVPATH, _FTYPE_ANY, 0, &connect_funcs, &io_funcs, &attr) == -1) {
        perror("resmgr_attach " COUNTER_DEVPATH);
        return EXIT_FAILURE;
    }
    printf("counter: serving %s\n", COUNTER_DEVPATH);
    fflush(stdout);

    dispatch_context_t *ctp = dispatch_context_alloc(dpp);
    for (;;) {
        if (!(ctp = dispatch_block(ctp))) { perror("dispatch_block"); return EXIT_FAILURE; }
        dispatch_handler(ctp);
    }
}
```

Two handlers we skipped are short: `io_close_ocb()` drops a closing client's armed notifications so a dead client cannot be woken, and `io_notify()` is the `ionotify()` entry, covered below.

## Build it for QNX and copy it over
_Duration: 3:00_

Three programs: the server (`counter`) and two clients (`counterctl`, `counterwatch`). `-Wall -Wextra -Werror` keeps the build honest. The `file` line proves the result is a QNX 8.0 binary (its interpreter is QNX's `ldqnx-64.so.2`), not a Mac or Linux one:

```bash
source env.sh
mkdir -p out
for p in counter counterctl counterwatch; do
  qnx qcc -Vgcc_ntox86_64 -std=gnu11 -Wall -Wextra -Werror -o out/$p src/$p.c && echo "built out/$p"
done
qnx file out/counter | cut -d, -f1-5
```

Output (recorded):

```text
built out/counter
built out/counterctl
built out/counterwatch
out/counter: ELF 64-bit LSB pie executable, x86-64, version 1 (SYSV), dynamically linked, interpreter /usr/lib/ldqnx-64.so.2
```

Copy the binaries to the guest:

```bash
source env.sh
tgtroot 'slay counter 2>/dev/null; true'      # stop an older copy, if any
tgt 'rm -rf ~/codelab'
tgtput out/counter out/counterctl out/counterwatch
tgt 'ls -l ~/codelab'
```

Output (recorded):

```text
total 72
-rwxr-xr-x  1 qnxuser qnxuser 14528 2026-11-16 01:02 counter
-rwxr-xr-x  1 qnxuser qnxuser  9536 2026-11-16 01:02 counterctl
-rwxr-xr-x  1 qnxuser qnxuser 13800 2026-11-16 01:02 counterwatch
```

## Run it
_Duration: 5:00_

A resource manager attaches a name in the **system path space**, which an ordinary user may not do. Try it as `qnxuser` first:

```bash
source env.sh
tgt 'cd ~/codelab && ./counter'
```

Output (recorded):

```text
resmgr_attach /dev/counter: Operation not permitted
```

`Operation not permitted` is QNX 8.0's security model working as intended: registering `/dev/counter` needs the path-space ability, which `root` has. Start it as root, in the background:

```bash
source env.sh
tgtroot 'slay counter 2>/dev/null
cd /data/home/qnxuser/codelab
nohup ./counter > counter.log 2>&1 < /dev/null &
sleep 1; cat counter.log'
tgt 'ls -l /dev/counter'
```

Output (recorded):

```text
counter: serving /dev/counter
crw-rw-rw-  1 root root 0,   1 2026-11-16 01:02 /dev/counter
```

`/dev/counter` is now a file in the guest. Use it like one, with no special client library:

```bash
source env.sh
tgt 'cat /dev/counter; echo inc > /dev/counter; echo inc > /dev/counter; cat /dev/counter'
```

Output (recorded):

```text
0
2
```

Our `write()` rejects words it does not know; the shell prints the `EINVAL` it got back:

```bash
source env.sh
tgt 'echo bogus > /dev/counter; echo "exit status: $?"'
```

Output (recorded):

```text
exit status: 1
bash: line 1: echo: write error: Invalid argument
```

Now the binary interface, through `counterctl`, which calls `devctl()`:

```bash
source env.sh
tgt '~/codelab/counterctl add 40; ~/codelab/counterctl get; ~/codelab/counterctl reset; cat /dev/counter'
```

Output (recorded):

```text
42
0
```

The server is an ordinary process, one you can see, stop and restart like any other (as `qnxuser`, `pidin` does not list root's processes, so these commands run as root). Kill it and the name disappears from the path space; start it again and it is back with a fresh counter:

```bash
source env.sh
tgtroot 'pidin -f an | grep "[.]/counter"'
tgtroot 'slay counter'
tgt 'cat /dev/counter'
```

Output (recorded):

```text
 5320733 ./counter
cat: /dev/counter: No such file or directory
```

```bash
source env.sh
tgtroot 'cd /data/home/qnxuser/codelab
nohup ./counter > counter.log 2>&1 < /dev/null &
sleep 1; cat counter.log'
tgt 'cat /dev/counter'
```

Output (recorded):

```text
counter: serving /dev/counter
0
```

## ionotify(): wait without polling
_Duration: 10:00_

A client that wants to know when the counter changes has two bad options: poll in a loop (wastes CPU, adds latency) or block in `read()` (cannot do anything else). QNX's answer is **`ionotify()`**: the client says "send me this pulse when `/dev/counter` has input", and goes to sleep in `MsgReceivePulse()`, where it can also receive messages from anyone else.

The server side has two parts. `io_notify()` tells the library which conditions are true *right now*; the library arms the client's event for the others. `counter_changed()` fires the armed events whenever the value changes:

```c
// src/counter.c
static int io_notify(resmgr_context_t *ctp, io_notify_t *msg, iofunc_ocb_t *ocb)
{
    struct counter_attr *a = ocb->attr;
    int trig = _NOTIFY_COND_OUTPUT;                   /* writes never block */
    if (a->unread) trig |= _NOTIFY_COND_INPUT;
    return iofunc_notify(ctp, msg, a->notify, trig, NULL, NULL);
}
```

```c
// src/counter.c
static void counter_changed(struct counter_attr *a)
{
    a->unread = 1;
    /* wake every client that armed a pulse for "input available" */
    iofunc_notify_trigger(a->notify, 1, IOFUNC_NOTIFY_INPUT);
}
```

The client creates a channel, connects to it, and builds a pulse event. Then comes the QNX 8.0 detail the first version of this codelab got wrong: **the event must be registered against the file descriptor with `MsgRegisterEvent()` before it is handed to `ionotify()`**. Without it everything appears to work (`ionotify()` returns 0, the server logs the trigger) and the pulse silently never arrives. It is called out in the `ionotify()` reference page, and took a debugging session to find. `POLLARM` is "tell me what is ready now, and arm the pulse only if nothing is", so the loop drains first and sleeps second:

```c
// src/counterwatch.c
int main(int argc, char **argv)
{
    int want = argc > 1 ? atoi(argv[1]) : 3;
    int fd = open(COUNTER_DEVPATH, O_RDWR);
    if (fd == -1) { perror("open " COUNTER_DEVPATH); return 1; }

    int chid = ChannelCreate(0);
    int coid = ConnectAttach(0, 0, chid, _NTO_SIDE_CHANNEL, 0);
    struct sigevent ev;
    SIGEV_PULSE_INIT(&ev, coid, SIGEV_PULSE_PRIO_INHERIT, PULSE_CODE, 0);
    /* QNX 8.0: an event handed to ionotify() must first be registered against that fd, otherwise the
     * resource manager's MsgDeliverEvent() has nothing it is allowed to deliver and no pulse ever arrives. */
    if (MsgRegisterEvent(&ev, fd) == -1) { perror("MsgRegisterEvent"); return 1; }

    printf("counterwatch: armed, waiting for %d change(s)\n", want);
    fflush(stdout);
    for (int seen = 0; seen < want;) {
        /* POLLARM: report conditions that are true now; arm the pulse only if input is NOT ready yet. */
        int now = ionotify(fd, _NOTIFY_ACTION_POLLARM, _NOTIFY_COND_INPUT, &ev);
        if (now == -1) { perror("ionotify"); return 1; }
        if (!(now & _NOTIFY_COND_INPUT)) {
            struct _pulse p;
            if (MsgReceivePulse(chid, &p, sizeof p, NULL) == -1) { perror("MsgReceivePulse"); return 1; }
            if (p.code != PULSE_CODE) continue;
        }
        print_value(fd);                         /* reading clears the device's "unread" flag */
        seen++;
    }
    return 0;
}
```

Run the watcher for three changes. A background job changes the counter from a second terminal-worth of work (a `write`, then a `devctl`, then another `write`) while `counterwatch` sleeps in between:

```bash
source env.sh
tgt 'cat /dev/counter >/dev/null'            # mark the current value as seen
( sleep 2; tgt 'echo inc > /dev/counter'; sleep 1; tgt '~/codelab/counterctl add 5'; sleep 1; tgt 'echo inc > /dev/counter' ) &
tgt '~/codelab/counterwatch 3'
wait
```

Output (recorded):

```text
counterwatch: armed, waiting for 3 change(s)
counterwatch: value is now 1
counterwatch: value is now 6
counterwatch: value is now 7
```

Three changes, three wake-ups, no polling: the watcher was blocked in the kernel between them. That is the whole mechanism behind `select()` and `poll()` on QNX too; they are built on `ionotify()`.

## Pitfalls and what to try next
_Duration: 2:00_

Things that went wrong while writing this codelab, so they need not go wrong for you:

- **No pulse arrives.** You forgot `MsgRegisterEvent(&ev, fd)`, or you armed with an event whose connection (`coid`) does not belong to the process that calls `MsgReceivePulse()`.
- **`cat /dev/counter` never ends.** Your `read()` does not advance `ocb->offset` / return 0 at the end.
- **`resmgr_attach: Operation not permitted`.** Not root. Resource managers that must run as a normal user get the path-space ability from a security policy (`secpol`); see QNX's *Security policies* docs.
- **`devctl()` seems to fail with `-1`.** It does not: `devctl()` returns the `errno` value itself (0 on success).
- **Handler "returns" a stack buffer.** The reply is sent after your handler returns; use `resmgr_msgwrite()` or static data.

To go further: give each *open file* its own state with an extended `ocb`; run the receive loop from a thread pool (`thread_pool_create`) instead of one thread; give the server a security policy so it does not need to be root; or trigger from a timer so the counter is a heartbeat. The Telltale repository this codelab ships in uses the same building blocks (`resmgr_attach`, message passing, `ionotify`) for a safety monitor; its README has the details.
