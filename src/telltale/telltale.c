/* telltale: the display-truth monitor.
 * Every period (timer pulse, SCHED_FIFO 60) it reads back what Screen composed for the display
 * (screen_read_display, display-manager context: the G1-FULL tier), decodes the speed digits and the
 * liveness tick, and checks them against the vehicle's true state from /dev/vehicle (truth window W).
 * On a lie it posts a safe layer with the true speed, decides hung / starved / Screen-blocked from the
 * renderer's draw-thread state (/proc, DCMD_PROC_TIDSTATUS), kills a hung or confirmed-wrong renderer
 * (respawned by its supervisor), verifies the new renderer from its own window (screen_read_window,
 * readable while covered), hands the display back, and writes an incident.
 * Resource manager (own thread): /dev/telltale (status text + devctls), /dev/telltale-events (incident CSV).
 * Env: TT_CONF (key=value file), TT_CHECKLOG (per-check CSV path), TT_EVDIR (incident CSV dir).
 * SPDX-License-Identifier: Apache-2.0 */
#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <signal.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <devctl.h>
#include <sys/iofunc.h>
#include <sys/dispatch.h>
#include <sys/neutrino.h>
#include <sys/procfs.h>
#include <sys/states.h>
#include <sys/slog2.h>
#include <secpol/secpol.h>
#include <screen/screen.h>
#include "../common/ttproto.h"
#include "../common/ttsec.h"
#include "../common/layout.h"
#include "../common/palette.h"
#include "../common/decode.h"
#include "../common/glyphs.h"
#include "../common/truth.h"
#include "../common/rcap.h"
#include "../common/disp.h"
#include "../common/text.h"

#define CK(x, m) do { if ((x) != 0) { fprintf(stderr, "telltale: %s: %s\n", m, strerror(errno)); exit(2); } } while (0)
#define MS 1000000ull

/* ---------- configuration (provisional defaults until the calibration run commits telltale.conf) ---------- */
static struct {
    int period_ms, W_ms, tick_stall_ms, h_max, truth_lost_ms, arm_grace_ms, restart_timeout_ms;
    int max_restarts, restart_window_s, mismatch_n, unknown_kill_ms, screen_stall_ms, truth_ok_n;
    int lamp_thr, lamp_mask;   /* lamp lit iff permille of lit-colour pixels >= lamp_thr; lamps checked = lamp_mask bits */
    int card_ms;               /* engineering mode: how long the incident card stays up after recovery */
} cfg = {20, 100, 0, GLYPH_H_MAX_DEFAULT, 200, 5000, 3000, 3, 60, 3, 0, 0, 5, 500, 0x3F, 5000};

static int conf_tick_stall_ms, conf_unknown_kill_ms, conf_screen_stall_ms;   /* as configured (0 = derive) */
static void cfg_derive(void)
{
    int rule = 2 * cfg.period_ms + 17;                 /* tick_stall_ms(p) = max(calibrated, 2p + 1 frame) */
    if (cfg.tick_stall_ms < rule) cfg.tick_stall_ms = rule;
    if (!cfg.unknown_kill_ms) cfg.unknown_kill_ms = 5 * cfg.tick_stall_ms;
    if (!cfg.screen_stall_ms) cfg.screen_stall_ms = 3 * cfg.period_ms;
}

static void cfg_load(const char *path)
{
    FILE *f = path ? fopen(path, "r") : NULL;
    if (!f) return;
    char line[160], k[64], val[32]; int v;
    while (fgets(line, sizeof line, f)) {
        if (line[0] == '#' || sscanf(line, " %63[^= ]=%31s", k, val) != 2) continue;
        char *end; long lv = strtol(val, &end, 0);   /* base 0: decimal, 0x hex */
        if (end == val || *end) { fprintf(stderr, "telltale: conf %s=%s is not an integer, ignored\n", k, val); continue; }
        v = (int)lv;
#define K(n) if (!strcmp(k, #n)) cfg.n = v;
        K(period_ms) K(W_ms) K(tick_stall_ms) K(h_max) K(truth_lost_ms) K(arm_grace_ms) K(restart_timeout_ms)
        K(max_restarts) K(restart_window_s) K(mismatch_n) K(unknown_kill_ms) K(screen_stall_ms) K(truth_ok_n) K(lamp_thr) K(lamp_mask) K(card_ms)
#undef K
        printf("telltale: conf %s=%d\n", k, v);
    }
    fclose(f);
}

/* the supported check period: the 4-bit renderer tick aliases after 16 frames (266 ms at 60 fps), and the
 * benched points are 10/20/50 ms, so periods outside 5..100 ms are refused (config and DCMD_TT_SET_PERIOD) */
#define PERIOD_MIN_MS 5
#define PERIOD_MAX_MS 100
static int period_ok(int p) { return p >= PERIOD_MIN_MS && p <= PERIOD_MAX_MS; }

/* reject out-of-range configuration loudly and keep the built-in default for that key */
static void cfg_check(void)
{
#define R(n, lo, hi, def) if (cfg.n < (lo) || cfg.n > (hi)) { \
        fprintf(stderr, "telltale: conf %s=%d out of range [%d, %d], using %d\n", #n, cfg.n, lo, hi, def); cfg.n = def; }
    R(period_ms, PERIOD_MIN_MS, PERIOD_MAX_MS, 20) R(W_ms, 0, 5000, 100) R(tick_stall_ms, 0, 60000, 0)
    R(truth_lost_ms, 1, 60000, 200) R(arm_grace_ms, 0, 600000, 5000) R(restart_timeout_ms, 100, 60000, 3000)
    R(max_restarts, 1, RCAP_N, 3) R(restart_window_s, 1, 3600, 60) R(mismatch_n, 1, 100000, 3)
    R(unknown_kill_ms, 0, 600000, 0) R(screen_stall_ms, 0, 600000, 0) R(truth_ok_n, 1, 1000, 5)
    R(lamp_thr, 0, 1000, 500) R(card_ms, 0, 600000, 5000)
#undef R
}

static struct itimerspec period_its(int ms)       /* normalized: tv_nsec stays in [0, 1e9) */
{
    struct itimerspec it;
    it.it_value.tv_sec = it.it_interval.tv_sec = ms / 1000;
    it.it_value.tv_nsec = it.it_interval.tv_nsec = (long)(ms % 1000) * 1000000L;
    return it;
}

/* ---------- shared state (resmgr thread <-> check loop) ---------- */
enum state { ST_WAIT, ST_NORMAL, ST_HOLD, ST_RESTART, ST_TRUTHLOST, ST_NODISPLAY, ST_GIVEUP };
static const char *ST[] = {"WAIT", "NORMAL", "SAFE-HOLD", "RESTARTING", "TRUTH-LOST", "NO-DISPLAY", "GIVEUP"};
enum verdict { V_PASS, V_TICK_STALL, V_BLANK, V_UNREADABLE, V_MISMATCH, V_TRUTH_LOST, V_NONE };
static const char *VN[] = {"PASS", "TICK_STALL", "BLANK", "UNREADABLE", "VALUE_MISMATCH", "TRUTH_LOST", "-"};
static const char *VCODE[] = {"OK", "FRZ", "BLK", "UNR", "VAL", "TRL", "-"};

struct incident {
    char id[16];
    enum verdict v;
    char cause[24];
    int shown, truth, restarts, old_pid, new_pid;
    unsigned lamps_shown, lamps_truth;   /* lamp bits read back / latest truth at detection */
    uint64_t t_last_verified, t_detect, t_safe_visible, t_kill, t_register, t_true;
    double exposure_ms;
};

static pthread_mutex_t g_mtx = PTHREAD_MUTEX_INITIALIZER;
static struct {
    int armed, debug, new_period;
    struct tt_register reg; int reg_new; uint64_t reg_t;
    int rearm;
    enum state st;
    unsigned long checks, faults, restarts_total, late, screen_stalls, capture_errors, ev_dropped;
    struct incident last; int have_last;
    struct incident open_inc; int have_open;   /* the incident in progress (not yet recovered), for cat /dev/telltale */
    unsigned long long truth_gaps;
    char ev[1 << 16]; size_t ev_len;
} g = {.armed = 1};

static atomic_ullong hb_start;      /* check start (0 = idle), for the Screen-stall guard */
static atomic_int guard_period_ms, guard_stall_ms;   /* the guard's copy of cfg, published by the check loop */
static int evfd = -1;
static slog2_buffer_t slog;        /* incidents also go to slog2 (buffer set "telltale"); NULL if slogger2 refused */

static void ev_append(const struct incident *in)
{
    char row[400];
    int n = snprintf(row, sizeof row, "%s,%s,%s,%d,%d,%llu,%llu,%.1f,%llu,%llu,%llu,%llu,%d,%.1f,%d,%d,0x%02x,0x%02x\n",
                     in->id, VN[in->v], in->cause, in->shown, in->truth, (unsigned long long)in->t_last_verified,
                     (unsigned long long)in->t_detect, (in->t_detect - in->t_last_verified) / 1e6,
                     (unsigned long long)in->t_safe_visible, (unsigned long long)in->t_kill,
                     (unsigned long long)in->t_register, (unsigned long long)in->t_true, in->restarts,
                     in->exposure_ms, in->old_pid, in->new_pid, in->lamps_shown, in->lamps_truth);
    if (n < 0) return;
    if ((size_t)n >= sizeof row) n = (int)sizeof row - 1;   /* truncated row: never write past the buffer */
    pthread_mutex_lock(&g_mtx);
    if (g.ev_len + (size_t)n < sizeof g.ev) { memcpy(g.ev + g.ev_len, row, (size_t)n); g.ev_len += (size_t)n; }
    else g.ev_dropped++;   /* the incidents.csv file still has the row; the node says how many it could not keep */
    g.last = *in; g.have_last = 1;
    pthread_mutex_unlock(&g_mtx);
    if (evfd >= 0 && tt_write_all(evfd, row, (size_t)n) < 0) perror("telltale: incident log");
    if (slog) slog2f(slog, 0, SLOG2_WARNING, "INCIDENT %s %s cause=%s shown=%d truth=%d caught_ms=%.1f restarts=%d",
                     in->id, VN[in->v], in->cause, in->shown, in->truth, (in->t_detect - in->t_last_verified) / 1e6, in->restarts);
    printf("telltale: INCIDENT %s", row); fflush(stdout);
}
static void open_publish(const struct incident *in)   /* NULL = no incident open */
{
    pthread_mutex_lock(&g_mtx);
    if (in) { g.open_inc = *in; g.have_open = 1; } else g.have_open = 0;
    pthread_mutex_unlock(&g_mtx);
}
#define EV_HEADER "id,verdict,cause,shown,truth,t_last_verified_ns,t_detect_ns,caught_ms,t_safe_visible_ns,t_kill_ns,t_register_ns,t_true_ns,restarts,exposure_ms,renderer_pid,new_pid,lamps_shown,lamps_truth\n"

/* ---------- resource manager: /dev/telltale and /dev/telltale-events ---------- */
static iofunc_attr_t attr_tt, attr_ev;
static secpol_permission_t *perm_register, *perm_control;
static int renderer_uid = 1000;   /* without a policy only this uid (TT_RENDERER_UID) or root may register as the renderer */
static uint32_t self_type;

static int allowed(resmgr_context_t *ctp, secpol_permission_t *p, int fallback)
{
    if (p) return secpol_check_permission(&ctp->info, self_type, p) == 0;
    return fallback;
}

static int io_read(resmgr_context_t *ctp, io_read_t *msg, iofunc_ocb_t *ocb)
{
    int st = iofunc_read_verify(ctp, msg, ocb, NULL);
    if (st != EOK) return st;
    if ((msg->i.xtype & _IO_XTYPE_MASK) != _IO_XTYPE_NONE) return ENOSYS;
    static char buf[(1 << 16) + 512];
    size_t n;
    pthread_mutex_lock(&g_mtx);
    if (ocb->attr == &attr_ev) {
        n = (size_t)snprintf(buf, sizeof buf, "%s", EV_HEADER);
        memcpy(buf + n, g.ev, g.ev_len); n += g.ev_len;
    } else {
        const struct incident *l = &g.last;
        n = (size_t)snprintf(buf, sizeof buf,
            "armed=%d state=%s period_ms=%d W_ms=%d tick_stall_ms=%d checks=%lu faults=%lu restarts=%lu late=%lu screen_stalls=%lu renderer_pid=%d capture_errors=%lu\n",
            g.armed, ST[g.st], cfg.period_ms, cfg.W_ms, cfg.tick_stall_ms, g.checks, g.faults, g.restarts_total, g.late,
            g.screen_stalls, g.reg.pid, g.capture_errors);
        n += (size_t)snprintf(buf + n, sizeof buf - n, "truth_gaps=%llu events_dropped=%lu\n", g.truth_gaps, g.ev_dropped);
        if (g.have_open) {
            const struct incident *o = &g.open_inc;
            n += (size_t)snprintf(buf + n, sizeof buf - n, "open=%s verdict=%s cause=%s shown=%d truth=%d caught_ms=%.1f restarts=%d\n",
                                  o->id, VN[o->v], o->cause, o->shown, o->truth, (o->t_detect - o->t_last_verified) / 1e6, o->restarts);
        }
        if (g.have_last)
            n += (size_t)snprintf(buf + n, sizeof buf - n,
                "last=%s verdict=%s cause=%s shown=%d truth=%d caught_ms=%.1f (since last verified frame) recovered=%s\n",
                l->id, VN[l->v], l->cause, l->shown, l->truth, (l->t_detect - l->t_last_verified) / 1e6, l->t_true ? "yes" : "no");
    }
    pthread_mutex_unlock(&g_mtx);
    size_t off = (size_t)ocb->offset, nb = 0;
    if (off < n) { nb = n - off; if (nb > msg->i.nbytes) nb = msg->i.nbytes; }
    ocb->offset += nb;
    MsgReply(ctp->rcvid, (long)nb, buf + off, nb);
    return _RESMGR_NOREPLY;
}

static int io_devctl(resmgr_context_t *ctp, io_devctl_t *msg, iofunc_ocb_t *ocb)
{
    int st = iofunc_devctl_default(ctp, msg, ocb);
    if (st != _RESMGR_DEFAULT) return st;
    if (ocb->attr != &attr_tt) return ENOSYS;
    void *data = _DEVCTL_DATA(msg->i);
    int rc = EOK;
    pthread_mutex_lock(&g_mtx);
    switch (msg->i.dcmd) {
    case DCMD_TT_REGISTER: {
        if (msg->i.nbytes < sizeof(struct tt_register)) { rc = EINVAL; break; }   /* never read past the client's payload */
        struct tt_register r; memcpy(&r, data, sizeof r);
        r.win_id[sizeof r.win_id - 1] = 0;
        /* the caller must be the process it registers, and (with a policy) of a type granted telltale:register */
        int ru = tt_client_euid(ctp->info.scoid);
        if (r.pid != ctp->info.pid || !allowed(ctp, perm_register, ru == 0 || ru == renderer_uid)) { rc = EPERM; break; }
        g.reg = r; g.reg_new = 1; g.reg_t = tt_now_ns();
        break;
    }
    case DCMD_TT_ARM: case DCMD_TT_DISARM: case DCMD_TT_REARM: case DCMD_TT_SET_PERIOD: case DCMD_TT_DEBUG:
        if ((msg->i.dcmd == DCMD_TT_SET_PERIOD || msg->i.dcmd == DCMD_TT_DEBUG) && msg->i.nbytes < sizeof(int32_t)) { rc = EINVAL; break; }
        if (!allowed(ctp, perm_control, tt_client_euid(ctp->info.scoid) == 0 || tt_client_euid(ctp->info.scoid) == (int)getuid())) { rc = EPERM; break; }
        if (msg->i.dcmd == DCMD_TT_ARM) g.armed = 1;
        else if (msg->i.dcmd == DCMD_TT_DISARM) g.armed = 0;
        else if (msg->i.dcmd == DCMD_TT_REARM) g.rearm = 1;
        else if (msg->i.dcmd == DCMD_TT_DEBUG) { int32_t d; memcpy(&d, data, sizeof d); g.debug = d; }
        else { int32_t p; memcpy(&p, data, sizeof p); if (!period_ok(p)) rc = EINVAL; else g.new_period = p; }
        break;
    default: rc = ENOSYS;
    }
    pthread_mutex_unlock(&g_mtx);
    if (rc != EOK) return rc;
    memset(&msg->o, 0, sizeof msg->o);
    return _RESMGR_PTR(ctp, &msg->o, sizeof msg->o);
}

static void *resmgr_thread(void *arg)
{
    (void)arg;
    struct sched_param sp = {.sched_priority = 30};
    pthread_setschedparam(pthread_self(), SCHED_FIFO, &sp);
    pthread_setname_np(0, "resmgr");
    dispatch_t *dpp = dispatch_create();
    resmgr_attr_t rattr = {.nparts_max = 1, .msg_max_size = 2048};
    static resmgr_connect_funcs_t cf; static resmgr_io_funcs_t iof;
    iofunc_func_init(_RESMGR_CONNECT_NFUNCS, &cf, _RESMGR_IO_NFUNCS, &iof);
    iof.read = io_read; iof.devctl = io_devctl;
    iofunc_attr_init(&attr_tt, S_IFCHR | 0666, NULL, NULL);
    iofunc_attr_init(&attr_ev, S_IFCHR | 0644, NULL, NULL);
    void *h = tt_policy_handle();
    if ((h ? secpol_resmgr_attach(h, dpp, &rattr, PATH_TELLTALE, _FTYPE_ANY, 0, &cf, &iof, &attr_tt, NULL)
           : resmgr_attach(dpp, &rattr, PATH_TELLTALE, _FTYPE_ANY, 0, &cf, &iof, &attr_tt)) == -1 ||
        (h ? secpol_resmgr_attach(h, dpp, &rattr, PATH_EVENTS, _FTYPE_ANY, 0, &cf, &iof, &attr_ev, NULL)
           : resmgr_attach(dpp, &rattr, PATH_EVENTS, _FTYPE_ANY, 0, &cf, &iof, &attr_ev)) == -1) {
        perror("telltale: resmgr_attach"); exit(1);
    }
    dispatch_context_t *ctp = dispatch_context_alloc(dpp);
    for (;;) {
        dispatch_context_t *c = dispatch_block(ctp);
        if (c) { ctp = c; dispatch_handler(ctp); }
        else if (errno != EINTR) { perror("telltale: dispatch_block"); usleep(100000); }   /* no busy loop at FIFO 30 */
    }
    return NULL;
}

/* Screen-stall guard (FIFO 61): a read-back call that has not returned within screen_stall_ms is reported */
static void *stall_guard(void *arg)
{
    (void)arg;
    struct sched_param sp = {.sched_priority = 61};
    pthread_setschedparam(pthread_self(), SCHED_FIFO, &sp);
    pthread_setname_np(0, "stallguard");
    uint64_t reported = 0;
    for (;;) {
        usleep((useconds_t)atomic_load(&guard_period_ms) * 1000);
        uint64_t s = atomic_load(&hb_start);
        if (s && s != reported && tt_now_ns() - s > (uint64_t)atomic_load(&guard_stall_ms) * MS) {
            reported = s;
            pthread_mutex_lock(&g_mtx); g.screen_stalls++; pthread_mutex_unlock(&g_mtx);
            printf("telltale: SCREEN_STALL check started %llu ms ago\n", (unsigned long long)((tt_now_ns() - s) / MS)); fflush(stdout);
        }
    }
    return NULL;
}

/* ---------- Screen ---------- */
static screen_context_t ctx_dm, ctx_app;
static screen_display_t disp;
static screen_window_t safe_win;
static int safe_visible;

static screen_pixmap_t mkpix(screen_context_t c, int w, int h, uint32_t **ptr, int *stride_px, screen_buffer_t *buf)
{
    screen_pixmap_t pix;
    CK(screen_create_pixmap(&pix, c), "create_pixmap");
    int usage = SCREEN_USAGE_READ | SCREEN_USAGE_WRITE | SCREEN_USAGE_NATIVE, fmt = SCREEN_FORMAT_RGBA8888, sz[2] = {w, h};
    CK(screen_set_pixmap_property_iv(pix, SCREEN_PROPERTY_USAGE, &usage), "pix usage");
    CK(screen_set_pixmap_property_iv(pix, SCREEN_PROPERTY_FORMAT, &fmt), "pix format");
    CK(screen_set_pixmap_property_iv(pix, SCREEN_PROPERTY_BUFFER_SIZE, sz), "pix size");
    CK(screen_create_pixmap_buffer(pix), "pix buffer");
    CK(screen_get_pixmap_property_pv(pix, SCREEN_PROPERTY_RENDER_BUFFERS, (void **)buf), "pix rbuf");
    void *p; int s;
    CK(screen_get_buffer_property_pv(*buf, SCREEN_PROPERTY_POINTER, &p), "pix ptr");
    CK(screen_get_buffer_property_iv(*buf, SCREEN_PROPERTY_STRIDE, &s), "pix stride");
    *ptr = p; *stride_px = s / 4;
    return pix;
}

static void drain_events(void)
{
    static screen_event_t ev;
    if (!ev) screen_create_event(&ev);
    for (int i = 0; i < 4096; i++) {
        if (screen_get_event(ctx_app, ev, 0) != 0) break;
        int type = SCREEN_EVENT_NONE;
        screen_get_event_property_iv(ev, SCREEN_PROPERTY_TYPE, &type);
        if (type == SCREEN_EVENT_NONE) break;
    }
}

static screen_window_t find_window(const char *want, int owner, int pos[2])
{
    int n = 0;
    if (screen_get_context_property_iv(ctx_app, SCREEN_PROPERTY_WINDOW_COUNT, &n) != 0 || n <= 0) return NULL;
    screen_window_t *w = calloc((size_t)n, sizeof *w), found = NULL;
    if (screen_get_context_property_pv(ctx_app, SCREEN_PROPERTY_WINDOWS, (void **)w) == 0)
        for (int i = 0; i < n && !found; i++) {
            char id[64] = "";
            screen_get_window_property_cv(w[i], SCREEN_PROPERTY_ID_STRING, sizeof id, id);
            int op = -1;
            if (strcmp(id, want) || screen_get_window_property_iv(w[i], SCREEN_PROPERTY_OWNER_PID, &op) != 0 || op != owner) continue;
            found = w[i]; screen_get_window_property_iv(found, SCREEN_PROPERTY_POSITION, pos);
        }
    free(w);
    return found;
}


/* the safe layer: true speed in the same cells with the same atlas, every lamp on in the truth, banner, marker.
 * One buffer: glass, banner and marker are painted once; each period redraws only the digit cells and lamps
 * and posts those rects (a full 1280x696 software redraw costs ~40 ms per period on QEMU TCG). */
#define SAFE_Z_UP 100
#define SAFE_Z_DOWN 1     /* below the cluster (z 10), which covers it completely */
static uint32_t *safe_px; static int safe_st; static screen_buffer_t safe_buf;

/* fault banner text. Driver mode: no metrics on the driver's glass. Engineering mode (ttctl debug on): the class and
 * this incident's measured caught time, labelled "ENGINEERING VIEW". Repainted only when the text changes. */
static int dbg_on;                          /* g.debug, copied once per period by the check loop */
static const struct incident *banner_inc;   /* the open incident (NULL when none) */
static char banner_cur[128];
static int banner_update(int lost)          /* returns 1 if the banner pixels changed */
{
    char t[128];
    const char *E = "ENGINEERING VIEW " TT_MIDDOT " ";
    if (dbg_on && banner_inc && lost)
        snprintf(t, sizeof t, "%sSPEED DATA LOST " TT_MIDDOT " %s", E, banner_inc->id);
    else if (dbg_on && banner_inc)
        snprintf(t, sizeof t, "%sDISPLAY FAULT CAUGHT " TT_MIDDOT " %s " TT_MIDDOT " %.0f ms", E, VN[banner_inc->v],
                 (banner_inc->t_detect - banner_inc->t_last_verified) / 1e6);
    else if (dbg_on)
        snprintf(t, sizeof t, "%sNO RENDERER FRAME " TT_MIDDOT " %s", E, lost ? "SPEED DATA LOST" : "SHOWING TRUE SPEED");
    else
        snprintf(t, sizeof t, "DISPLAY FAULT " TT_MIDDOT " %s", lost ? "SPEED DATA LOST" : "SHOWING TRUE SPEED");
    if (!strcmp(t, banner_cur)) return 0;
    snprintf(banner_cur, sizeof banner_cur, "%s", t);
    const struct tt_font *f = &tt_font_banner;
    uint32_t *row = safe_px + (long)L_BANNER_Y * safe_st;
    tt_fill(row, safe_st, L_SAFE_W, L_BANNER_H, PAL_AMBER);
    int w = tt_text_width(f, t), x = (L_SAFE_W - w) / 2;
    tt_text_draw(row, safe_st, L_SAFE_W, L_BANNER_H, x, (L_BANNER_H - f->h) / 2, f, t, 0xFF000000u | PAL_GLASS, 0xFF000000u | PAL_AMBER);
    printf("telltale: BANNER x=%d w=%d mode=%s text=\"%s\"\n", x, w, dbg_on ? "engineering" : "driver", t); fflush(stdout);
    return 1;
}

static void safe_init(void)
{
    screen_buffer_t rb[1];
    CK(screen_get_window_property_pv(safe_win, SCREEN_PROPERTY_RENDER_BUFFERS, (void **)rb), "safe render buffer");
    void *p; int stride;
    CK(screen_get_buffer_property_pv(rb[0], SCREEN_PROPERTY_POINTER, &p), "safe ptr");
    CK(screen_get_buffer_property_iv(rb[0], SCREEN_PROPERTY_STRIDE, &stride), "safe stride");
    safe_buf = rb[0]; safe_px = p; safe_st = stride / 4;
    tt_fill(safe_px, safe_st, L_SAFE_W, L_SAFE_H, PAL_GLASS);
    banner_update(0);
    for (int y = 0; y < L_MARK_S; y++)
        for (int x = 0; x < L_MARK_S; x++) safe_px[(long)(L_MARK_Y + y) * safe_st + L_MARK_X + x] = 0xFF000000u | tt_marker_px(x, y);
}

static uint64_t safe_ns;   /* cost of the last safe-layer update (per-check CSV) */
static int safe_allowed = 1;   /* 0 while disarmed: observe-only means no safe-layer post in any state */
static void safe_show_(const struct veh_state *s, int lost, int pos[2]);
static void safe_show(const struct veh_state *s, int lost, int pos[2])
{
    if (!safe_allowed) return;
    uint64_t t = tt_now_ns();
    safe_show_(s, lost, pos);
    safe_ns += tt_now_ns() - t;
}
static int safe_last_v = -2, safe_last_lamps = -1;
static void safe_show_(const struct veh_state *s, int lost, int pos[2])
{
    uint32_t *px = safe_px; int st = safe_st;
    int c[3], v = lost || !s ? -1 : disp_kmh(s->speed_kmh), lamps = s && !lost ? s->lamps : 0;
    /* Screen's software composition of a post costs ~35 ms on QEMU TCG: post only when the content changes */
    int bchg = banner_update(v < 0);
    if (safe_visible && v == safe_last_v && lamps == safe_last_lamps && !bchg) return;
    safe_last_v = v; safe_last_lamps = lamps;
    tt_cells_for(v, c);
    for (int i = 0; i < L_DIGIT_N; i++)
        tt_blit_glyph(px + (long)L_DIGIT_Y * st + L_DIGIT_X0 + i * GLYPH_W, st, c[i], PAL_LIT, PAL_GLASS);
    for (int i = 0; i < L_LAMP_N; i++)
        tt_fill(px + (long)L_LAMP_Y * st + L_LAMP[i].x, st, L_LAMP_S, L_LAMP_S, s && !lost && ((s->lamps >> i) & 1) ? L_LAMP[i].rgb : PAL_GLASS);
    int full[4] = {0, 0, L_SAFE_W, L_SAFE_H};
    int dirty[12] = {L_DIGIT_X0, L_DIGIT_Y, L_DIGIT_N * GLYPH_W, GLYPH_H, 0, L_LAMP_Y, L_LAMP_W, L_LAMP_H, 0, L_BANNER_Y, L_SAFE_W, L_BANNER_H};
    if (!safe_visible) {            /* raise: always VISIBLE, hidden by sitting under the opaque cluster window */
        int z = SAFE_Z_UP, vis = 1;
        screen_set_window_property_iv(safe_win, SCREEN_PROPERTY_VISIBLE, &vis);   /* undo safe_off() */
        screen_set_window_property_iv(safe_win, SCREEN_PROPERTY_POSITION, pos);
        screen_set_window_property_iv(safe_win, SCREEN_PROPERTY_ZORDER, &z);
        safe_visible = 1;
        screen_post_window(safe_win, safe_buf, 1, full, 0);
        screen_flush_context(ctx_app, 0);
    } else screen_post_window(safe_win, safe_buf, bchg ? 3 : 2, dirty, 0);
}

static void safe_hide(void)
{
    if (!safe_visible) return;
    /* invisible, not only under the cluster: when the renderer's window vanishes (crash) the composed read must see
     * empty glass, never the stale truth of an earlier incident still painted in this buffer */
    int z = SAFE_Z_DOWN, vis = 0;
    screen_set_window_property_iv(safe_win, SCREEN_PROPERTY_ZORDER, &z);
    screen_set_window_property_iv(safe_win, SCREEN_PROPERTY_VISIBLE, &vis);
    screen_flush_context(ctx_app, 0);
    safe_visible = 0;
}

static void safe_off(void)   /* disarm: really invisible, also when no opaque renderer window is there to cover it */
{
    int z = SAFE_Z_DOWN, vis = 0;
    screen_set_window_property_iv(safe_win, SCREEN_PROPERTY_ZORDER, &z);
    screen_set_window_property_iv(safe_win, SCREEN_PROPERTY_VISIBLE, &vis);
    screen_flush_context(ctx_app, 0);
    safe_visible = 0;
}

/* ---------- incident card (engineering mode): monitor-owned top-z window, ui.md S4 ----------
 * Shown for card_ms after an incident closes (the renderer is verified true again), right of the digit block and clear
 * of every monitored rect, so the monitored frame never carries it. Hidden the same way as the safe layer (z under the
 * opaque cluster). Every value on it is the closed incident's. */
#define CARD_Z_UP 110
static screen_window_t card_win; static screen_buffer_t card_buf; static uint32_t *card_px; static int card_st;
static int card_up; static uint64_t card_until; static char card_id[16];
static void card_init(void)
{
    screen_buffer_t rb[1]; void *p; int stride;
    CK(screen_get_window_property_pv(card_win, SCREEN_PROPERTY_RENDER_BUFFERS, (void **)rb), "card render buffer");
    CK(screen_get_buffer_property_pv(rb[0], SCREEN_PROPERTY_POINTER, &p), "card ptr");
    CK(screen_get_buffer_property_iv(rb[0], SCREEN_PROPERTY_STRIDE, &stride), "card stride");
    card_buf = rb[0]; card_px = p; card_st = stride / 4;
    tt_fill(card_px, card_st, L_CARD_W, L_CARD_H, PAL_CARD);
}
static void card_show(const struct incident *in, const int wpos[2])
{
    char r[6][96];
    double caught = (in->t_detect - in->t_last_verified) / 1e6, restored = (in->t_true - in->t_last_verified) / 1e6;
    snprintf(r[0], sizeof r[0], "INCIDENT %s", in->id);
    if (strcmp(in->cause, "-")) snprintf(r[1], sizeof r[1], "%s " TT_MIDDOT " %s", VN[in->v], in->cause);
    else snprintf(r[1], sizeof r[1], "%s", VN[in->v]);     /* no cause classified (no stall seen) */
    if (in->shown == in->truth && in->lamps_shown != in->lamps_truth)
        snprintf(r[2], sizeof r[2], "lamps shown %02x " TT_MIDDOT " truth %02x", in->lamps_shown, in->lamps_truth);
    else if (in->shown >= 0) snprintf(r[2], sizeof r[2], "shown %d " TT_MIDDOT " truth %d km/h", in->shown, in->truth);
    else snprintf(r[2], sizeof r[2], "shown -- " TT_MIDDOT " truth %d km/h", in->truth);
    snprintf(r[3], sizeof r[3], "caught %.0f ms " TT_MIDDOT " restored %.0f ms", caught, restored);
    if (in->t_kill || (in->new_pid > 0 && in->new_pid != in->old_pid))   /* killed by us, or crashed and re-registered */
        snprintf(r[4], sizeof r[4], "restarted: pid %d -> %d", in->old_pid, in->new_pid);
    else snprintf(r[4], sizeof r[4], "handed back, no restart");
    snprintf(r[5], sizeof r[5], "service: cat /dev/telltale");
    const struct tt_font *f = &tt_font_card;
    const uint32_t bg = 0xFF000000u | PAL_CARD, fg[6] = {PAL_AMBER, PAL_LIT, PAL_LIT, PAL_LIT, PAL_LIT, PAL_MUTED};
    tt_fill(card_px, card_st, L_CARD_W, L_CARD_H, PAL_CARD);
    tt_fill(card_px, card_st, L_CARD_W, 4, PAL_AMBER);
    for (int i = 0; i < 6; i++)
        tt_text_draw(card_px, card_st, L_CARD_W, L_CARD_H, L_CARD_PAD, L_CARD_PAD + i * L_CARD_PITCH, f, r[i], 0xFF000000u | fg[i], bg);
    int full[4] = {0, 0, L_CARD_W, L_CARD_H}, pos[2] = {wpos[0] + L_CARD_X, wpos[1] + L_CARD_Y}, z = CARD_Z_UP;
    screen_set_window_property_iv(card_win, SCREEN_PROPERTY_POSITION, pos);
    screen_set_window_property_iv(card_win, SCREEN_PROPERTY_ZORDER, &z);
    screen_post_window(card_win, card_buf, 1, full, 0);
    screen_flush_context(ctx_app, 0);
    card_up = 1; card_until = tt_now_ns() + (uint64_t)cfg.card_ms * MS;
    snprintf(card_id, sizeof card_id, "%s", in->id);
    printf("telltale: CARD shown %s at %d,%d %dx%d rows=\"%s|%s|%s|%s|%s|%s\"\n", in->id, pos[0], pos[1], L_CARD_W, L_CARD_H,
           r[0], r[1], r[2], r[3], r[4], r[5]);
    fflush(stdout);
}
static void card_hide(const char *why)
{
    if (!card_up) return;
    int z = SAFE_Z_DOWN;
    screen_set_window_property_iv(card_win, SCREEN_PROPERTY_ZORDER, &z);
    screen_flush_context(ctx_app, 0);
    card_up = 0;
    printf("telltale: CARD hidden %s (%s)\n", card_id, why); fflush(stdout);
}

/* ---------- cause check: the renderer's draw-thread state ---------- */
enum cause { C_NONE, C_HUNG, C_STARVED, C_SCREEN, C_UNKNOWN };
static const char *CN[] = {"-", "hung", "starved", "screen-blocked", "unknown"};

static int thread_state(int pid, int tid)
{
    char path[64];
    snprintf(path, sizeof path, "/proc/%d/ctl", pid);
    int fd = open(path, O_RDONLY);
    if (fd < 0) { snprintf(path, sizeof path, "/proc/%d/as", pid); fd = open(path, O_RDONLY); }
    if (fd < 0) return -1;
    procfs_status s; memset(&s, 0, sizeof s);
    s.tid = tid;
    int rc = devctl(fd, DCMD_PROC_TIDSTATUS, &s, sizeof s, NULL);
    close(fd);
    return rc == EOK && (int)s.tid == tid ? (int)s.state : -1;
}

static enum cause classify(const int *st, int n)
{
    int hung = 0, ready = 0, sendrep = 0, other = 0;
    for (int i = 0; i < n; i++) {
        switch (st[i]) {
        case STATE_MUTEX: case STATE_CONDVAR: case STATE_SEM: case STATE_JOIN: hung++; break;
        case STATE_READY: case STATE_RUNNING: ready++; break;
        case STATE_SEND: case STATE_REPLY: sendrep++; break;
        default: other++;
        }
    }
    if (hung == n) return C_HUNG;
    if (ready == n) return C_STARVED;
    if (sendrep && sendrep + ready == n) return C_SCREEN;
    return C_UNKNOWN;
}

/* restart cap: max_restarts kills inside restart_window_s, shared by every kill site (ring in rcap.h) */
static int cap_reached(uint64_t now, const struct rcap *kills)
{
    return rcap_reached(kills, now, (uint64_t)cfg.restart_window_s * 1000 * MS, cfg.max_restarts);
}

/* ---------- the check loop ---------- */
struct shot { enum speed_kind kind; int value, tick, marker; unsigned lamps; };

static void decode_at(const uint32_t *base, int st, int dx, int dy, int tx, int ty, struct shot *o)
{
    int c[3], d;
    for (int i = 0; i < 3; i++) c[i] = tt_decode_cell(base + (long)dy * st + dx + i * GLYPH_W, st, cfg.h_max, &d);
    o->kind = tt_combine_cells(c, &o->value);
    o->tick = tx >= 0 ? tt_tick_value(base + (long)ty * st + tx, st) : -1;
}

static int vfd = -1, vfd_lost;
static struct truth T;
static void drain_truth(void)
{
    static struct veh_since q;
    if (vfd < 0) {
        if ((vfd = open(PATH_VEHICLE, O_RDONLY)) < 0) return;
        if (vfd_lost) {   /* a restarted vehicled numbers its samples from 1 again: drop the old window and watermark */
            truth_init(&T); vfd_lost = 0;
            printf("telltale: reconnected to %s (truth window reset)\n", PATH_VEHICLE); fflush(stdout);
        }
    }
    q.after_seq = T.last_seq;
    uint64_t to = 10 * MS;                                    /* bounds a SEND-blocked call (vehicled not receiving) */
    TimerTimeout(CLOCK_MONOTONIC, _NTO_TIMEOUT_SEND | _NTO_TIMEOUT_REPLY, NULL, &to, NULL);
    int rc = devctl(vfd, DCMD_VEH_GET_SINCE, &q, sizeof q, NULL);
    if (rc != EOK) {
        if (rc != ETIMEDOUT && rc != EINTR) {   /* the connection is dead (vehicled exited): reconnect on a later check */
            printf("telltale: %s: %s -- reconnecting\n", PATH_VEHICLE, strerror(rc)); fflush(stdout);
            close(vfd); vfd = -1; vfd_lost = 1;
        }
        return;
    }
    for (uint32_t i = 0; i < q.n && i < VEH_RING; i++) truth_push(&T, &q.s[i]);
}

static enum verdict judge(const struct shot *s, uint64_t t_lo, uint64_t t_hi, int stalled, int *tmin, int *tmax)
{
    *tmin = *tmax = -1;
    if (stalled) return V_TICK_STALL;
    if (s->kind == SPD_BLANK) return V_BLANK;
    if (s->kind == SPD_UNREADABLE) return V_UNREADABLE;
    if (!truth_match_speed(&T, s->value, t_lo, t_hi, tmin, tmax)) return V_MISMATCH;
    /* a lamp that differs from every sample in the window is a lie too (VALUE_MISMATCH, region = lamps) */
    return truth_match_lamps(&T, s->lamps, (unsigned)cfg.lamp_mask, t_lo, t_hi) ? V_PASS : V_MISMATCH;
}

int main(void)
{
    cfg_load(getenv("TT_CONF"));
    cfg_check();
    conf_tick_stall_ms = cfg.tick_stall_ms; conf_unknown_kill_ms = cfg.unknown_kill_ms; conf_screen_stall_ms = cfg.screen_stall_ms;
    cfg_derive();
    atomic_store(&guard_period_ms, cfg.period_ms); atomic_store(&guard_stall_ms, cfg.screen_stall_ms);
    {   /* slog2 before tt_enter drops privileges; failure is reported, never fatal */
        slog2_buffer_set_config_t sc = {.num_buffers = 1, .buffer_set_name = "telltale", .verbosity_level = SLOG2_INFO,
                                        .buffer_config = {{.buffer_name = "incidents", .num_pages = 4}}};
        if (slog2_register(&sc, &slog, 0) == -1) { perror("telltale: slog2_register"); slog = NULL; }
    }
    tt_enter("telltale", SCHED_FIFO, 60);
    pthread_setname_np(0, "check");
    if (tt_policy_loaded()) {
        perm_register = secpol_get_permission(tt_policy_handle(), "telltale", "register", 0);
        perm_control = secpol_get_permission(tt_policy_handle(), "telltale", "control", 0);
    }
    self_type = secpol_type_id();
    if (getenv("TT_RENDERER_UID")) renderer_uid = atoi(getenv("TT_RENDERER_UID"));
    const char *evdir = getenv("TT_EVDIR") ? getenv("TT_EVDIR") : "/tmp/tt/ev";
    char path[256]; snprintf(path, sizeof path, "%s/incidents.csv", evdir);
    evfd = open(path, O_WRONLY | O_APPEND | O_CREAT, 0644);
    FILE *chk = getenv("TT_CHECKLOG") ? fopen(getenv("TT_CHECKLOG"), "w") : NULL;
    if (chk) fprintf(chk, "t_cap_start_ns,t_cap_end_ns,check_us,state,armed,verdict,decoded,truth_min,truth_max,tick,marker,win_verdict,win_decoded,win_us,safe_us,lamps,win_lamps\n");
    printf("telltale: period_ms=%d W_ms=%d tick_stall_ms=%d h_max=%d mismatch_n=%d unknown_kill_ms=%d restart_timeout_ms=%d policy=%s slog2=%s\n",
           cfg.period_ms, cfg.W_ms, cfg.tick_stall_ms, cfg.h_max, cfg.mismatch_n, cfg.unknown_kill_ms, cfg.restart_timeout_ms,
           perm_register ? "secpol" : "none (uid/pid checks)", slog ? "telltale/incidents" : "off");
    fflush(stdout);

    /* display-manager context only (no window-manager role: with a WM registered Screen leaves z-order and
     * visibility to it, and the safe layer would never compose on top); the renderer's window handle comes
     * from the application context's window list, which the renderer's read grant opens to us */
    CK(screen_create_context(&ctx_dm, SCREEN_DISPLAY_MANAGER_CONTEXT), "display-manager context (needs root)");
    CK(screen_create_context(&ctx_app, SCREEN_APPLICATION_CONTEXT), "app context");
    int n = 0; CK(screen_get_context_property_iv(ctx_dm, SCREEN_PROPERTY_DISPLAY_COUNT, &n), "display count");
    screen_display_t *ds = calloc((size_t)n, sizeof *ds);
    CK(screen_get_context_property_pv(ctx_dm, SCREEN_PROPERTY_DISPLAYS, (void **)ds), "displays");
    disp = ds[0];
    int dsz[2] = {0, 0}; screen_get_display_property_iv(disp, SCREEN_PROPERTY_SIZE, dsz);
    printf("telltale: display %dx%d, tier G1-FULL (screen_read_display, display-manager context)\n", dsz[0], dsz[1]); fflush(stdout);

    uint32_t *pb, *pt, *pw, *pwt, *pl, *pwl; int sb, stt, sw, swt, sl, swl; screen_buffer_t bb, bt, bw, bwt, bl, bwl;
    mkpix(ctx_dm, L_BLOCK_W, L_BLOCK_H, &pb, &sb, &bb);   /* composed digit + gear block */
    mkpix(ctx_dm, L_TICK_S, L_TICK_S, &pt, &stt, &bt);    /* composed tick */
    mkpix(ctx_app, L_BLOCK_W, L_BLOCK_H, &pw, &sw, &bw);   /* the renderer's own window (same context as its handle): digit block ... */
    mkpix(ctx_app, L_TICK_S, L_TICK_S, &pwt, &swt, &bwt);  /* ... and tick (renderer-sourced reads) */
    mkpix(ctx_dm, L_LAMP_W, L_LAMP_H, &pl, &sl, &bl);      /* composed lamp row */
    mkpix(ctx_app, L_LAMP_W, L_LAMP_H, &pwl, &swl, &bwl);  /* the renderer's own lamp row */

    CK(screen_create_window(&safe_win, ctx_app), "safe window");
    {
        int usage = SCREEN_USAGE_WRITE | SCREEN_USAGE_NATIVE, fmt = SCREEN_FORMAT_RGBA8888, sz[2] = {L_SAFE_W, L_SAFE_H};
        int z = SAFE_Z_DOWN, vis = 1, pos[2] = {L_WIN_X, L_WIN_Y};
        CK(screen_set_window_property_iv(safe_win, SCREEN_PROPERTY_USAGE, &usage), "safe usage");
        CK(screen_set_window_property_iv(safe_win, SCREEN_PROPERTY_FORMAT, &fmt), "safe format");
        CK(screen_set_window_property_iv(safe_win, SCREEN_PROPERTY_BUFFER_SIZE, sz), "safe bufsize");
        CK(screen_set_window_property_iv(safe_win, SCREEN_PROPERTY_SIZE, sz), "safe size");
        CK(screen_set_window_property_iv(safe_win, SCREEN_PROPERTY_POSITION, pos), "safe pos");
        CK(screen_set_window_property_iv(safe_win, SCREEN_PROPERTY_ZORDER, &z), "safe z");
        vis = 0;   /* hidden = invisible (safe_hide); the first raise makes it visible */
        CK(screen_set_window_property_iv(safe_win, SCREEN_PROPERTY_VISIBLE, &vis), "safe visible");
        CK(screen_set_window_property_cv(safe_win, SCREEN_PROPERTY_ID_STRING, 13, "tt-safe-layer"), "safe id");
        CK(screen_create_window_buffers(safe_win, 1), "safe buffers");
        safe_init();
        int full[4] = {0, 0, L_SAFE_W, L_SAFE_H};
        screen_post_window(safe_win, safe_buf, 1, full, 0);
    }
    CK(screen_create_window(&card_win, ctx_app), "card window");
    {
        int usage = SCREEN_USAGE_WRITE | SCREEN_USAGE_NATIVE, fmt = SCREEN_FORMAT_RGBA8888, sz[2] = {L_CARD_W, L_CARD_H};
        int z = SAFE_Z_DOWN, vis = 1, pos[2] = {L_WIN_X + L_CARD_X, L_WIN_Y + L_CARD_Y};
        CK(screen_set_window_property_iv(card_win, SCREEN_PROPERTY_USAGE, &usage), "card usage");
        CK(screen_set_window_property_iv(card_win, SCREEN_PROPERTY_FORMAT, &fmt), "card format");
        CK(screen_set_window_property_iv(card_win, SCREEN_PROPERTY_BUFFER_SIZE, sz), "card bufsize");
        CK(screen_set_window_property_iv(card_win, SCREEN_PROPERTY_SIZE, sz), "card size");
        CK(screen_set_window_property_iv(card_win, SCREEN_PROPERTY_POSITION, pos), "card pos");
        CK(screen_set_window_property_iv(card_win, SCREEN_PROPERTY_ZORDER, &z), "card z");
        CK(screen_set_window_property_iv(card_win, SCREEN_PROPERTY_VISIBLE, &vis), "card visible");
        CK(screen_set_window_property_cv(card_win, SCREEN_PROPERTY_ID_STRING, 16, "tt-incident-card"), "card id");
        CK(screen_create_window_buffers(card_win, 1), "card buffers");
        card_init();
        int full[4] = {0, 0, L_CARD_W, L_CARD_H};
        screen_post_window(card_win, card_buf, 1, full, 0);
    }

    pthread_t th;
    pthread_create(&th, NULL, resmgr_thread, NULL);
    pthread_create(&th, NULL, stall_guard, NULL);

    int chid = ChannelCreate(_NTO_CHF_PRIVATE), coid = ConnectAttach(0, 0, chid, _NTO_SIDE_CHANNEL, 0);
    struct sigevent tev; SIGEV_PULSE_INIT(&tev, coid, 60, _PULSE_CODE_MINAVAIL, 0);
    timer_t tid; CK(timer_create(CLOCK_MONOTONIC, &tev, &tid), "timer_create");
    struct itimerspec its = period_its(cfg.period_ms);
    CK(timer_settime(tid, 0, &its, NULL), "timer_settime");

    truth_init(&T);
    struct tt_register rend = {0};
    screen_window_t rwin = NULL;
    int rpos[2] = {L_WIN_X, L_WIN_Y};
    enum state st = ST_WAIT;
    uint64_t t0 = tt_now_ns(), t_last_verified = 0, t_tick_change = 0, t_tick_real = 0, t_prev_loop = 0, t_state = t0, t_reg_seen = 0;
    int last_tick = -1, tick_moves_since_reg = 0, mismatch_cnt = 0, nowin_cnt = 0, fresh_cnt = 0, killed_pid = 0;
    int cstates[16], ncs = 0;
    struct rcap kills; rcap_reset(&kills);
    struct incident inc; int inc_open = 0, armed = 1, prev_armed = 1, seq_inc = 0;
    int cap_errs = 0;                /* composed reads failed in a row */
    enum cause cause = C_NONE;
    uint64_t t_unknown = 0;          /* when the current cause became UNKNOWN: the UNKNOWN kill counts from here */
    int readerr = 0;                 /* this check's window verdict is UNREADABLE because screen_read_window failed */

    for (;;) {
        struct _pulse p;
        if (MsgReceivePulse(chid, &p, sizeof p, NULL) == -1) {
            if (errno != EINTR) { perror("telltale: MsgReceivePulse"); usleep((useconds_t)cfg.period_ms * 1000); }
            continue;
        }
        uint64_t now = tt_now_ns();
        atomic_store(&hb_start, now);
        int late = t_prev_loop && now - t_prev_loop >= 266 * MS;   /* >= 16 frames: the 4-bit tick may alias */
        t_prev_loop = now;
        drain_events();

        pthread_mutex_lock(&g_mtx);
        armed = g.armed; dbg_on = g.debug;
        int newreg = g.reg_new; struct tt_register reg = g.reg; g.reg_new = 0;
        if (g.rearm) { g.rearm = 0; rcap_reset(&kills); if (st == ST_GIVEUP) { st = ST_HOLD; t_state = now; } }
        if (g.new_period) {
            int np = g.new_period; g.new_period = 0;
            struct itimerspec ni = period_its(np);
            if (timer_settime(tid, 0, &ni, NULL) == 0) {
                /* re-derive from the CONFIGURED values (telltale.conf), never below them: the rule only raises a floor */
                cfg.period_ms = np;
                cfg.tick_stall_ms = conf_tick_stall_ms; cfg.unknown_kill_ms = conf_unknown_kill_ms; cfg.screen_stall_ms = conf_screen_stall_ms;
                cfg_derive();
                atomic_store(&guard_period_ms, cfg.period_ms); atomic_store(&guard_stall_ms, cfg.screen_stall_ms);
            } else { printf("telltale: timer_settime(%d ms): %s -- period unchanged\n", np, strerror(errno)); fflush(stdout); }
        }
        if (late) g.late++;
        pthread_mutex_unlock(&g_mtx);

        safe_allowed = armed;
        if (prev_armed && !armed) {   /* DISARM = observe-only in EVERY state (ttproto.h): no safe layer, no kill, no incident */
            if (inc_open) { snprintf(inc.cause, sizeof inc.cause, "disarmed"); ev_append(&inc); inc_open = 0; banner_inc = NULL; open_publish(NULL); }
            safe_off(); card_hide("disarmed");
            if (st != ST_NORMAL && st != ST_WAIT) {
                printf("telltale: disarmed in %s -> %s\n", ST[st], rwin ? ST[ST_NORMAL] : ST[ST_WAIT]); fflush(stdout);
                st = rwin ? ST_NORMAL : ST_WAIT; t_state = now;
            }
            mismatch_cnt = 0; nowin_cnt = 0; cause = C_NONE;
        }
        prev_armed = armed;

        if (newreg && reg.pid != rend.pid) {
            rend = reg; rwin = NULL; tick_moves_since_reg = 0; t_reg_seen = now;
            if (inc_open && !inc.t_register) { inc.t_register = now; inc.new_pid = reg.pid; }
            printf("telltale: renderer registered pid=%d draw_tid=%d window=%s\n", reg.pid, reg.draw_tid, reg.win_id); fflush(stdout);
        }
        if (rend.pid && !rwin) {
            rwin = find_window(rend.win_id, rend.pid, rpos);
            if (rwin) { nowin_cnt = 0; printf("telltale: window %s found at %d,%d\n", rend.win_id, rpos[0], rpos[1]); fflush(stdout); }
        }
        drain_truth();
        if (!rwin) {   /* no renderer window to read: boot wait or after a kill */
            const struct veh_state *lt = truth_latest(&T);
            enum state prev_nw = st;
            int fresh_nw = lt && (lt->t_ns >= now || now - lt->t_ns <= (uint64_t)cfg.truth_lost_ms * MS);
            if (st == ST_WAIT && armed && now - t0 > (uint64_t)cfg.arm_grace_ms * MS) {
                if (fresh_nw) { st = ST_NODISPLAY; t_state = now; printf("telltale: NO_DISPLAY\n"); }
                else { st = ST_TRUTHLOST; fresh_cnt = 0; t_state = now; printf("telltale: NO_TRUTH (no fresh vehicle sample after arm_grace_ms)\n"); }
            }
            if (st == ST_TRUTHLOST) {   /* no window, no fresh truth: dashes up; truth back and no incident open -> NO-DISPLAY */
                safe_show(lt, !fresh_nw, rpos);
                if (fresh_nw && !inc_open) { st = ST_NODISPLAY; t_state = now; }
            }
            if ((st == ST_NODISPLAY || st == ST_RESTART || st == ST_GIVEUP) && lt) safe_show(lt, lt->t_ns < now && now - lt->t_ns > (uint64_t)cfg.truth_lost_ms * MS, rpos);
            /* SAFE-HOLD but the live renderer's window is gone or unreadable: keep the truth up, and after mismatch_n
             * periods restart it (cause no-window) rather than hold forever */
            if (st == ST_HOLD && rend.pid && armed) {
                if (lt) safe_show(lt, lt->t_ns < now && now - lt->t_ns > (uint64_t)cfg.truth_lost_ms * MS, rpos);
                if (kill(rend.pid, 0) == -1 && errno == ESRCH) {   /* the renderer died with its window already gone */
                    if (inc_open && !strcmp(inc.cause, "-")) snprintf(inc.cause, sizeof inc.cause, "crash");
                    killed_pid = rend.pid; st = ST_RESTART; t_state = now; nowin_cnt = 0;
                    printf("telltale: renderer pid %d is gone (crash, no window)\n", rend.pid); fflush(stdout);
                } else if (cause != C_STARVED && cause != C_SCREEN && ++nowin_cnt >= cfg.mismatch_n && cap_reached(now, &kills)) {
                    st = ST_GIVEUP; t_state = now; nowin_cnt = 0;   /* never kill a starved / Screen-blocked renderer; cap applies here too */
                    if (inc_open) { snprintf(inc.cause, sizeof inc.cause, "TT-GIVEUP"); ev_append(&inc); open_publish(&inc); }
                    printf("telltale: TT-GIVEUP (no-window)\n"); fflush(stdout);
                } else if (cause != C_STARVED && cause != C_SCREEN && nowin_cnt >= cfg.mismatch_n) {
                    if (inc_open && !strcmp(inc.cause, "-")) snprintf(inc.cause, sizeof inc.cause, "no-window");
                    rcap_note(&kills, now); if (inc_open) inc.restarts++;
                    killed_pid = rend.pid; if (inc_open) inc.t_kill = tt_now_ns();
                    kill(rend.pid, SIGKILL);
                    pthread_mutex_lock(&g_mtx); g.restarts_total++; pthread_mutex_unlock(&g_mtx);
                    printf("telltale: SIGKILL renderer pid %d (no-window)\n", rend.pid); fflush(stdout);
                    st = ST_RESTART; t_state = now; nowin_cnt = 0;
                }
            }
            if (st == ST_RESTART && now - t_state > (uint64_t)cfg.restart_timeout_ms * MS) {
                printf("telltale: restart timeout (pid %d did not register)\n", killed_pid); fflush(stdout);
                st = ST_NODISPLAY; t_state = now;
            }
            if (st != prev_nw) { printf("telltale: %s -> %s\n", ST[prev_nw], ST[st]); fflush(stdout); }
            pthread_mutex_lock(&g_mtx); g.checks++; g.st = st; g.truth_gaps = T.gaps; pthread_mutex_unlock(&g_mtx);   /* status stays truthful without a window */
            atomic_store(&hb_start, 0);
            continue;
        }

        /* composed read: digit block + tick, display coordinates = window position + layout offsets */
        int rb[4] = {rpos[0] + L_BLOCK_X, rpos[1] + L_BLOCK_Y, L_BLOCK_W, L_BLOCK_H};
        int rt[4] = {rpos[0] + L_TICK_X, rpos[1] + L_TICK_Y, L_TICK_S, L_TICK_S};
        uint64_t t_cap_start = tt_now_ns();
        int rl[4] = {rpos[0] + L_LAMP_X, rpos[1] + L_LAMP_Y, L_LAMP_W, L_LAMP_H};
        int e1 = screen_read_display(disp, bb, 1, rb, 0), e2 = screen_read_display(disp, bt, 1, rt, 0);
        if (!e1 && !e2) e2 = screen_read_display(disp, bl, 1, rl, 0);
        uint64_t t_cap_end = tt_now_ns();
        drain_truth();                           /* samples that arrived while we sat in Screen still count */
        if (e1 || e2) {
            printf("telltale: screen_read_display: %s\n", strerror(errno)); fflush(stdout);
            /* the composed display cannot be verified: after mismatch_n failed reads in a row say so (NO-DISPLAY) and keep
             * the truth up as best effort; never a renderer kill (the fault is in the read path, not proven in the renderer) */
            if (++cap_errs >= cfg.mismatch_n && st != ST_NODISPLAY && st != ST_GIVEUP) {
                printf("telltale: CAPTURE_LOST (%d composed reads failed) %s -> %s\n", cap_errs, ST[st], ST[ST_NODISPLAY]); fflush(stdout);
                if (slog) slog2f(slog, 0, SLOG2_WARNING, "CAPTURE_LOST state=%s", ST[st]);
                st = ST_NODISPLAY; t_state = now;
            }
            if (cap_errs >= cfg.mismatch_n && st == ST_NODISPLAY) {
                const struct veh_state *elt = truth_latest(&T); uint64_t en = tt_now_ns();
                safe_show(elt, !(elt && (elt->t_ns >= en || en - elt->t_ns <= (uint64_t)cfg.truth_lost_ms * MS)), rpos);
            }
            pthread_mutex_lock(&g_mtx); g.capture_errors++; g.st = st; pthread_mutex_unlock(&g_mtx);
            atomic_store(&hb_start, 0);
            continue;
        }
        cap_errs = 0;
        struct shot cs;
        decode_at(pb, sb, L_DIGIT_X0 - L_BLOCK_X, L_DIGIT_Y - L_BLOCK_Y, -1, 0, &cs);
        cs.tick = tt_tick_value(pt, stt);
        cs.lamps = tt_lamps_decode(pl, sl, cfg.lamp_thr);
        cs.marker = tt_marker_ok(pb + (long)(L_MARK_Y - L_BLOCK_Y) * sb + (L_MARK_X - L_BLOCK_X), sb);

        if (late) { last_tick = cs.tick; t_tick_change = t_cap_end; }
        else if (cs.tick != last_tick) { last_tick = cs.tick; t_tick_change = t_tick_real = t_cap_end; tick_moves_since_reg++; }   /* t_tick_real: a genuine change, never a lateness reset */
        int stalled = t_tick_change && t_cap_end - t_tick_change > (uint64_t)cfg.tick_stall_ms * MS;
        if (!t_tick_change) t_tick_change = t_cap_end;
        /* cause samples: while the tick has not moved for more than one period */
        if (t_cap_end - t_tick_change > (uint64_t)cfg.period_ms * MS) {   /* rolling: the latest 16 draw-thread states of this stall */
            if (ncs == 16) { memmove(cstates, cstates + 1, 15 * sizeof cstates[0]); ncs = 15; }
            cstates[ncs++] = thread_state(rend.pid, rend.draw_tid);
        }
        else ncs = 0;

        const struct veh_state *lt = truth_latest(&T);
        uint64_t tnow = tt_now_ns();
        int truth_fresh = lt && (lt->t_ns >= tnow || tnow - lt->t_ns <= (uint64_t)cfg.truth_lost_ms * MS);
        int tmin, tmax;
        uint64_t t_lo = t_cap_start - (uint64_t)cfg.W_ms * MS;
        enum verdict cv = judge(&cs, t_lo, t_cap_end, stalled, &tmin, &tmax);
        if (!truth_fresh) cv = V_TRUTH_LOST;

        /* renderer-sourced read (its own window; readable while the safe layer covers it) */
        enum verdict wv = V_NONE; struct shot ws = {SPD_UNREADABLE, -1, -1, 0, 0};
        readerr = 0;                             /* per check: set only when THIS check's window read failed */
        uint64_t t_win0 = tt_now_ns(), win_ns = 0; safe_ns = 0;
        if (st == ST_HOLD || st == ST_RESTART || st == ST_NODISPLAY || st == ST_TRUTHLOST || st == ST_GIVEUP) {
            int wb[4] = {L_BLOCK_X, L_BLOCK_Y, L_BLOCK_W, L_BLOCK_H}, wt[4] = {L_TICK_X, L_TICK_Y, L_TICK_S, L_TICK_S};
            int wl[4] = {L_LAMP_X, L_LAMP_Y, L_LAMP_W, L_LAMP_H};
            if (screen_read_window(rwin, bw, 1, wb, 0) == 0 && screen_read_window(rwin, bwt, 1, wt, 0) == 0 &&
                screen_read_window(rwin, bwl, 1, wl, 0) == 0) {
                decode_at(pw, sw, L_DIGIT_X0 - L_BLOCK_X, L_DIGIT_Y - L_BLOCK_Y, -1, 0, &ws);
                ws.tick = tt_tick_value(pwt, swt);
                ws.lamps = tt_lamps_decode(pwl, swl, cfg.lamp_thr);
                int a, b;
                wv = truth_fresh ? judge(&ws, t_lo, tt_now_ns(), stalled, &a, &b) : V_TRUTH_LOST;
            } else {
                printf("telltale: screen_read_window: %s\n", strerror(errno)); fflush(stdout);
                rwin = NULL;
                if (st == ST_HOLD) { wv = V_UNREADABLE; readerr = 1; }   /* frame cannot be verified: counts towards the kill unless starved */
                if (st == ST_HOLD && kill(rend.pid, 0) == -1 && errno == ESRCH) {   /* the renderer died on its own */
                    if (inc_open && !strcmp(inc.cause, "-")) snprintf(inc.cause, sizeof inc.cause, "crash");
                    killed_pid = rend.pid; st = ST_RESTART; t_state = now;
                    printf("telltale: renderer pid %d is gone (crash)\n", rend.pid); fflush(stdout);
                    atomic_store(&hb_start, 0);
                    continue;
                }
            }
        }

        win_ns = tt_now_ns() - t_win0;
        if (inc_open && !inc.t_safe_visible && cs.marker) inc.t_safe_visible = t_cap_end;
        uint64_t since_last_verified = t_last_verified;
        int truth_disp = lt ? disp_kmh(lt->speed_kmh) : -1;
#define OPEN(V) do { memset(&inc, 0, sizeof inc); inc.v = (V); inc.shown = cs.kind == SPD_VALUE ? cs.value : -1; inc.truth = truth_disp; inc.lamps_shown = cs.lamps; inc.lamps_truth = lt ? lt->lamps : 0; \
            inc.t_last_verified = since_last_verified ? since_last_verified : t0; inc.t_detect = t_cap_end; inc.old_pid = rend.pid; \
            snprintf(inc.id, sizeof inc.id, "TT-%s-%04d", VCODE[V], ++seq_inc); snprintf(inc.cause, sizeof inc.cause, "-"); inc_open = 1; banner_inc = &inc; card_hide("new incident"); \
            pthread_mutex_lock(&g_mtx); g.faults++; g.open_inc = inc; g.have_open = 1; pthread_mutex_unlock(&g_mtx); \
            printf("telltale: DETECT %s %s shown=%d truth=%d lamps_shown=0x%02x lamps_truth=0x%02x caught_ms=%.1f\n", inc.id, VN[V], inc.shown, inc.truth, cs.lamps, lt ? lt->lamps : 0, (inc.t_detect - inc.t_last_verified) / 1e6); fflush(stdout); } while (0)
#define CLOSE() do { if (inc_open) { inc.t_true = t_cap_end; inc.exposure_ms = ((inc.t_safe_visible ? inc.t_safe_visible : inc.t_true) - inc.t_last_verified) / 1e6; ev_append(&inc); open_publish(NULL); inc_open = 0; banner_inc = NULL; \
            if (dbg_on) card_show(&inc, rpos); } } while (0)

        enum state prev = st;
        switch (st) {
        case ST_WAIT: case ST_NODISPLAY:            /* arm after the first truth sample + first tick change + one true frame */
            if (truth_fresh && tick_moves_since_reg > 0 && (st == ST_WAIT ? cv : wv) == V_PASS) {
                safe_hide(); st = ST_NORMAL; t_last_verified = t_cap_end; CLOSE();
                printf("telltale: ARMED renderer pid=%d (window %s)\n", rend.pid, rend.win_id); fflush(stdout);
            } else if (st == ST_WAIT && armed && now - t0 > (uint64_t)cfg.arm_grace_ms * MS) {
                if (truth_fresh) st = ST_NODISPLAY;
                else { st = ST_TRUTHLOST; fresh_cnt = 0; safe_show(lt, 1, rpos); printf("telltale: NO_TRUTH (no fresh vehicle sample after arm_grace_ms)\n"); fflush(stdout); }
            }
            if (st == ST_NODISPLAY && lt) safe_show(lt, !truth_fresh, rpos);
            break;
        case ST_NORMAL:
            if (cv == V_PASS) { t_last_verified = t_cap_end; break; }
            if (!armed) break;                          /* observe-only: rows continue, no action */
            OPEN(cv);
            if (cv == V_TRUTH_LOST) { st = ST_TRUTHLOST; fresh_cnt = 0; safe_show(lt, 1, rpos); break; }
            st = ST_HOLD; mismatch_cnt = 0; cause = C_NONE;
            safe_show(lt, 0, rpos);
            break;
        case ST_HOLD:
            if (!truth_fresh) { st = ST_TRUTHLOST; fresh_cnt = 0; safe_show(lt, 1, rpos); break; }
            safe_show(lt, 0, rpos);
            /* classify on any stall during the hold, whatever opened the incident: a renderer frozen while the
             * speed changes is first seen as VALUE_MISMATCH, then its tick stops */
            /* re-classified on every stalled check (latest 3 states), so a renderer that is starved first and then
             * really hangs (or the reverse) gets the action for what it is now, never a cause frozen at first sight */
            enum cause nc = stalled && ncs >= 3 ? classify(cstates + ncs - 3, 3) : cause;
            if (nc != cause) {
                cause = nc;
                if (cause == C_UNKNOWN) t_unknown = now;
                snprintf(inc.cause, sizeof inc.cause, "%s", CN[cause]);
                printf("telltale: cause %s (draw thread states %d,%d,%d)\n", CN[cause], cstates[ncs - 3], cstates[ncs - 2], cstates[ncs - 1]); fflush(stdout);
            }
            if (wv == V_PASS && !stalled) {             /* renderer-sourced frame true and its tick moving: hand back */
                safe_hide(); st = ST_NORMAL; t_last_verified = t_cap_end; CLOSE(); break;
            }
            int kill_now = 0;
            /* a wrong VALUE only counts towards a kill once the renderer's tick has moved since detection, i.e. it is
             * live and drawing wrong ("live-wrong"). A frozen or starved renderer also shows a stale value while the speed
             * moves, but its tick has stopped: wait for the stall and its cause (hung -> kill, starved -> no kill) instead
             * of racing it. Blank / unreadable frames are a lie whatever the tick does. */
            if ((wv == V_MISMATCH && t_tick_real > inc.t_detect && cause != C_STARVED && cause != C_SCREEN) ||
                wv == V_BLANK || (wv == V_UNREADABLE && !(readerr && (cause == C_STARVED || cause == C_SCREEN)))) {
                if (++mismatch_cnt >= cfg.mismatch_n) kill_now = 1;
            }
            if (stalled && cause == C_HUNG) kill_now = 1;
            /* UNKNOWN only kills after unknown_kill_ms of UNKNOWN itself, so one mixed state window in a long STARVE
             * (READY, READY, SEND) cannot kill a starved renderer */
            if (stalled && cause == C_UNKNOWN && now - t_unknown > (uint64_t)cfg.unknown_kill_ms * MS) kill_now = 1;
            if (!armed) kill_now = 0;
            if (kill_now) {
                if (cap_reached(now, &kills)) {   /* the incident will not recover on its own: write its record now */
                    st = ST_GIVEUP; snprintf(inc.cause, sizeof inc.cause, "TT-GIVEUP"); ev_append(&inc); open_publish(&inc);
                    printf("telltale: TT-GIVEUP\n"); fflush(stdout); break;
                }
                if (!strcmp(inc.cause, "-")) snprintf(inc.cause, sizeof inc.cause, "%s", wv == V_MISMATCH ? "live-wrong" : VN[wv]);
                rcap_note(&kills, now); inc.restarts++;   /* restarts = kills the monitor sent in THIS incident */
                killed_pid = rend.pid; inc.t_kill = tt_now_ns();
                kill(rend.pid, SIGKILL);
                pthread_mutex_lock(&g_mtx); g.restarts_total++; pthread_mutex_unlock(&g_mtx);
                printf("telltale: SIGKILL renderer pid %d (%s)\n", rend.pid, inc.cause); fflush(stdout);
                rwin = NULL; st = ST_RESTART;
            }
            break;
        case ST_RESTART:
            safe_show(lt, !truth_fresh, rpos);
            if (rend.pid != killed_pid && tick_moves_since_reg > 0 && wv == V_PASS) { safe_hide(); st = ST_NORMAL; t_last_verified = t_cap_end; CLOSE(); }
            else if (now - t_state > (uint64_t)cfg.restart_timeout_ms * MS && rend.pid == killed_pid) { st = ST_NODISPLAY; }
            else if (rend.pid != killed_pid && t_reg_seen && now - t_reg_seen > (uint64_t)cfg.restart_timeout_ms * MS) {
                /* the restarted renderer registered but has not shown one verified frame in restart_timeout_ms: back to
                 * SAFE-HOLD, where the one kill site, the stall classifier and the restart cap decide (never hold forever) */
                printf("telltale: restarted renderer pid %d not verified in %d ms -> SAFE-HOLD\n", rend.pid, cfg.restart_timeout_ms); fflush(stdout);
                st = ST_HOLD; mismatch_cnt = 0; cause = C_NONE;
            }
            break;
        case ST_TRUTHLOST:
            safe_show(lt, 1, rpos);
            if (truth_fresh) {
                if (++fresh_cnt >= cfg.truth_ok_n) {
                    if (inc_open) { st = ST_HOLD; mismatch_cnt = 0; cause = C_UNKNOWN; t_unknown = now; }
                    else st = ST_NODISPLAY;
                }
            } else fresh_cnt = 0;
            break;
        case ST_GIVEUP:
            safe_show(lt, !truth_fresh, rpos);
            break;
        }
        if (card_up && tt_now_ns() > card_until) card_hide("timeout");
        if (st != prev) {
            t_state = now;
            printf("telltale: %s -> %s\n", ST[prev], ST[st]); fflush(stdout);
        }
        uint64_t t_end = tt_now_ns();
        pthread_mutex_lock(&g_mtx); g.checks++; g.st = st; g.truth_gaps = T.gaps; pthread_mutex_unlock(&g_mtx);
        if (chk) fprintf(chk, "%llu,%llu,%llu,%s,%d,%s,%d,%d,%d,%d,%d,%s,%d,%llu,%llu,0x%02x,0x%02x\n", (unsigned long long)t_cap_start, (unsigned long long)t_cap_end,
                         (unsigned long long)((t_end - t_cap_start) / 1000), ST[st], armed, VN[cv], cs.kind == SPD_VALUE ? cs.value : -1 - (int)cs.kind,
                         tmin, tmax, cs.tick, cs.marker, VN[wv], ws.kind == SPD_VALUE ? ws.value : -1 - (int)ws.kind,
                         (unsigned long long)(win_ns / 1000), (unsigned long long)(safe_ns / 1000), cs.lamps, ws.lamps);
        static unsigned flushn;
        if (chk && ++flushn % 25 == 0) fflush(chk);
        atomic_store(&hb_start, 0);
    }
}
