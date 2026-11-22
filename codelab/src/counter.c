/* counter.c - a tiny QNX 8.0 resource manager: /dev/counter
 *
 *   read()     -> the current value as text ("7\n"); a second read on the same fd returns 0 bytes (EOF)
 *   write()    -> "inc" adds 1, "reset" sets 0 (anything else, also an over-long or NUL-carrying write: EINVAL)
 *   devctl()   -> DCMD_COUNTER_GET / _ADD / _RESET (see counter.h)
 *   ionotify() -> a client can ask for a pulse whenever the value changed since it was last read (by any client)
 *
 * Build: qcc -Vgcc_ntox86_64 -Wall -Wextra -Werror -o counter counter.c   Run as root (it registers a name).
 */
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/types.h>

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

static resmgr_connect_funcs_t connect_funcs;
static resmgr_io_funcs_t      io_funcs;
static struct counter_attr    attr;

static void counter_changed(struct counter_attr *a)
{
    a->unread = 1;
    /* wake every client that armed a pulse for "input available" */
    iofunc_notify_trigger(a->notify, 1, IOFUNC_NOTIFY_INPUT);
}

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

static int io_write(resmgr_context_t *ctp, io_write_t *msg, iofunc_ocb_t *ocb)
{
    int s = iofunc_write_verify(ctp, msg, ocb, NULL);
    if (s != EOK) return s;
    if ((msg->i.xtype & _IO_XTYPE_MASK) != _IO_XTYPE_NONE) return ENOSYS;

    char cmd[16] = {0};
    if (msg->i.nbytes >= sizeof cmd) return EINVAL;  /* longer than any command: refuse it, never truncate it */
    size_t n = msg->i.nbytes;
    if (resmgr_msgread(ctp, cmd, n, sizeof msg->i) != (int)n) return EFAULT;
    if (memchr(cmd, '\0', n)) return EINVAL;          /* "inc\0junk" must not pass as "inc" */
    while (n && (cmd[n - 1] == '\n' || cmd[n - 1] == ' ')) cmd[--n] = 0;

    if (!strcmp(cmd, "inc"))        ocb->attr->value++;
    else if (!strcmp(cmd, "reset")) ocb->attr->value = 0;
    else return EINVAL;
    counter_changed(ocb->attr);

    _IO_SET_WRITE_NBYTES(ctp, msg->i.nbytes);
    return _RESMGR_NPARTS(0);
}

static int io_devctl(resmgr_context_t *ctp, io_devctl_t *msg, iofunc_ocb_t *ocb)
{
    int s = iofunc_devctl_default(ctp, msg, ocb);     /* the standard devctls first */
    if (s != _RESMGR_DEFAULT) return s;

    struct counter_attr *a = ocb->attr;
    int nbytes = 0;
    switch (msg->i.dcmd) {
    case DCMD_COUNTER_GET:
        if (msg->i.nbytes < sizeof(uint64_t)) return EINVAL;  /* the reply must fit the client's buffer */
        *(uint64_t *)_DEVCTL_DATA(msg->o) = a->value;
        nbytes = sizeof(uint64_t);
        break;
    case DCMD_COUNTER_ADD:
        if (msg->i.nbytes != sizeof(int32_t)) return EINVAL;   /* the client's struct must be the size we expect */
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

/* ionotify()/select() land here. We tell the library which conditions are true RIGHT NOW. If one the client
 * asked about is true it reports that and arms nothing; if none is, it arms the client's event (a pulse) and
 * counter_changed() fires it later. */
static int io_notify(resmgr_context_t *ctp, io_notify_t *msg, iofunc_ocb_t *ocb)
{
    struct counter_attr *a = ocb->attr;
    int trig = _NOTIFY_COND_OUTPUT;                   /* writes never block */
    if (a->unread) trig |= _NOTIFY_COND_INPUT;
    return iofunc_notify(ctp, msg, a->notify, trig, NULL, NULL);
}

static int io_close_ocb(resmgr_context_t *ctp, void *reserved, iofunc_ocb_t *ocb)
{
    iofunc_notify_remove(ctp, ocb->attr->notify);     /* drop this client's armed events */
    return iofunc_close_ocb_default(ctp, reserved, ocb);
}

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
