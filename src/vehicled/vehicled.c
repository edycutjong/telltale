/* vehicled: the /dev/vehicle resource manager -- the vehicle's true state, for the renderer and the monitor.
 *   read                     one text line: seq=.. t_ns=.. trace_s=.. speed_kmh=.. gear=D lamps=0x05
 *   devctl DCMD_VEH_GET       latest struct veh_state
 *   devctl DCMD_VEH_GET_SINCE every retained sample (last 64) with seq > after_seq
 *   devctl DCMD_VEH_SET       publish a sample: only clients the security policy grants `vehicle write`
 *                            (feeder_t); without a loaded policy only uid $TT_FEEDER_UID (default 0) may write
 *   ionotify(_NOTIFY_COND_INPUT) pulse on every new sample (edge: arm with _NOTIFY_ACTION_TRANARM)
 * SCHED_FIFO 50.  SPDX-License-Identifier: Apache-2.0 */
#include <errno.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/iofunc.h>
#include <sys/dispatch.h>
#include <sys/neutrino.h>
#include <secpol/secpol.h>
#include "../common/ttproto.h"
#include "../common/ttsec.h"

static struct veh_state ring[VEH_RING];
static uint64_t seq;
static iofunc_notify_t notify[3];
static secpol_permission_t *perm_write;
static uint32_t self_type;
static int feeder_uid;
static unsigned long denied;
static const char GEARS[] = "PRND";

static const struct veh_state *latest(void) { return seq ? &ring[(seq - 1) % VEH_RING] : NULL; }

static int may_write(resmgr_context_t *ctp)
{
    if (perm_write) return secpol_check_permission(&ctp->info, self_type, perm_write) == 0;
    return tt_client_euid(ctp->info.scoid) == feeder_uid;
}

static int io_read(resmgr_context_t *ctp, io_read_t *msg, iofunc_ocb_t *ocb)
{
    int st = iofunc_read_verify(ctp, msg, ocb, NULL);
    if (st != EOK) return st;
    if ((msg->i.xtype & _IO_XTYPE_MASK) != _IO_XTYPE_NONE) return ENOSYS;
    char line[192];
    const struct veh_state *s = latest();
    int n = s ? snprintf(line, sizeof line, "seq=%llu t_ns=%llu trace_s=%.3f speed_kmh=%.2f gear=%c lamps=0x%02x denied=%lu\n",
                         (unsigned long long)s->seq, (unsigned long long)s->t_ns, s->trace_s, s->speed_kmh,
                         GEARS[s->gear & 3], s->lamps, denied)   /* denied: writes refused (policy / uid / range) */
              : snprintf(line, sizeof line, "seq=0 (no sample yet) denied=%lu\n", denied);
    size_t off = (size_t)ocb->offset, nb = 0;
    if (off < (size_t)n) { nb = (size_t)n - off; if (nb > msg->i.nbytes) nb = msg->i.nbytes; }
    ocb->offset += nb;
    MsgReply(ctp->rcvid, (long)nb, line + off, nb);
    return _RESMGR_NOREPLY;
}

static int io_devctl(resmgr_context_t *ctp, io_devctl_t *msg, iofunc_ocb_t *ocb)
{
    int st = iofunc_devctl_default(ctp, msg, ocb);
    if (st != _RESMGR_DEFAULT) return st;
    void *data = _DEVCTL_DATA(msg->i);
    size_t nbytes = 0;
    switch (msg->i.dcmd) {
    case DCMD_VEH_GET: {
        if (msg->i.nbytes < sizeof(struct veh_state)) return EINVAL;   /* the reply must fit the client's buffer */
        const struct veh_state *s = latest();
        if (!s) return EAGAIN;
        memcpy(data, s, sizeof *s); nbytes = sizeof *s; break;
    }
    case DCMD_VEH_GET_SINCE: {
        if (msg->i.nbytes < sizeof(struct veh_since)) return EINVAL;   /* request + reply share the client's buffer */
        struct veh_since *q = data;
        uint64_t after = q->after_seq, first = seq > VEH_RING ? seq - VEH_RING + 1 : 1;
        if (after + 1 > first) first = after + 1;
        uint32_t n = 0;
        for (uint64_t k = first; k <= seq; k++) q->s[n++] = ring[(k - 1) % VEH_RING];
        q->n = n;
        nbytes = offsetof(struct veh_since, s) + n * sizeof(struct veh_state);
        break;
    }
    case DCMD_VEH_SET: {
        if (!may_write(ctp)) { denied++; return EPERM; }
        if (msg->i.nbytes < sizeof(struct veh_state)) return EINVAL;   /* never read past the client's payload */
        struct veh_state in;
        memcpy(&in, data, sizeof in);
        /* a sample the cluster could not show is refused, never stored: finite speed in [-50, 500] km/h, gear P/R/N/D */
        if (!(in.speed_kmh >= -50.0f && in.speed_kmh <= 500.0f) || in.gear > GEAR_D)   /* NaN fails both compares */ { denied++; return EINVAL; }
        in.seq = ++seq;
        in.t_ns = tt_now_ns();
        ring[(in.seq - 1) % VEH_RING] = in;
        iofunc_notify_trigger(notify, 1, IOFUNC_NOTIFY_INPUT);   /* every armed client gets a pulse */
        break;
    }
    default:
        return ENOSYS;
    }
    memset(&msg->o, 0, sizeof msg->o);
    msg->o.nbytes = (uint32_t)nbytes;
    return _RESMGR_PTR(ctp, &msg->o, sizeof msg->o + nbytes);
}

static int io_notify(resmgr_context_t *ctp, io_notify_t *msg, iofunc_ocb_t *ocb)
{
    (void)ocb;
    return iofunc_notify(ctp, msg, notify, 0, NULL, NULL); /* edge-triggered: never "already ready" */
}

static int io_close_ocb(resmgr_context_t *ctp, void *reserved, iofunc_ocb_t *ocb)
{
    iofunc_notify_remove(ctp, notify);
    return iofunc_close_ocb_default(ctp, reserved, ocb);
}

int main(void)
{
    tt_enter("vehicled", SCHED_FIFO, 50);
    feeder_uid = getenv("TT_FEEDER_UID") ? atoi(getenv("TT_FEEDER_UID")) : 0;
    /* without a loaded policy secpol_get_permission() still succeeds (it checks environment variables), so use it only with one */
    perm_write = tt_policy_loaded() ? secpol_get_permission(tt_policy_handle(), "vehicle", "write", 0) : NULL;
    self_type = secpol_type_id();
    if (perm_write) printf("vehicled: DCMD_VEH_SET guarded by secpol permission vehicle:write (object type id %u)\n", self_type);
    else printf("vehicled: no security policy loaded: DCMD_VEH_SET guarded by uid %d\n", feeder_uid);

    dispatch_t *dpp = dispatch_create();
    if (!dpp) { perror("dispatch_create"); return 1; }
    resmgr_attr_t rattr = {.nparts_max = 1, .msg_max_size = 8192};
    resmgr_connect_funcs_t cf; resmgr_io_funcs_t iof;
    iofunc_func_init(_RESMGR_CONNECT_NFUNCS, &cf, _RESMGR_IO_NFUNCS, &iof);
    iof.read = io_read; iof.devctl = io_devctl; iof.notify = io_notify; iof.close_ocb = io_close_ocb;
    static iofunc_attr_t attr;
    iofunc_attr_init(&attr, S_IFCHR | 0666, NULL, NULL);
    IOFUNC_NOTIFY_INIT(notify);
    int id = tt_policy_handle()
        ? secpol_resmgr_attach(tt_policy_handle(), dpp, &rattr, PATH_VEHICLE, _FTYPE_ANY, 0, &cf, &iof, &attr, NULL)
        : resmgr_attach(dpp, &rattr, PATH_VEHICLE, _FTYPE_ANY, 0, &cf, &iof, &attr);
    if (id == -1) { perror("resmgr_attach " PATH_VEHICLE); return 1; }
    printf("vehicled: serving %s\n", PATH_VEHICLE); fflush(stdout);
    dispatch_context_t *ctp = dispatch_context_alloc(dpp);
    for (;;) {
        if (!(ctp = dispatch_block(ctp))) { perror("dispatch_block"); return 1; }
        dispatch_handler(ctp);
    }
}
