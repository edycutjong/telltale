/* cluster: the instrument-cluster renderer under test -- a QNX Screen window (1280x720, fixed layout),
 * double-buffered, full redraw every frame, software blits from the shared D-DIN digit atlas.
 *   data thread (SCHED_RR 21): ionotify pulse from /dev/vehicle -> DCMD_VEH_GET -> shared state (mutex A)
 *   draw thread (SCHED_RR 20): ~60 fps; copies the state, draws digits, gear strip, lamps, tick; posts
 *   main thread: /dev/cluster-ctl -- DCMD_CL_FAULT test hooks (documented as such; the injector's only door):
 *     FREEZE   a real lock-order inversion between the draw and data threads (barrier-forced)
 *     BLANK    posts a cleared frame (arg 1: hides the window instead)
 *     STALE    the data thread stops applying samples; drawing (and the tick) continue
 *     MISDRAW  intends v, draws v with its digits reversed (48 -> 84); the tick continues
 *     CRASH    SIGSEGV in the draw thread       HEAL  clears BLANK / STALE / MISDRAW
 * Registers {pid, draw tid, window id} with the monitor at start (DCMD_TT_REGISTER).
 * Effective fault times go to $TT_EVDIR/cluster-bench.csv (O_APPEND) before the fault takes effect.
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
#include <screen/screen.h>
#include "../common/ttproto.h"
#include "../common/ttsec.h"
#include "../common/layout.h"
#include "../common/palette.h"
#include "../common/decode.h"
#include "../common/glyphs.h"
#include "../common/disp.h"

#define CK(x, m) do { if ((x) != 0) { fprintf(stderr, "cluster: %s: %s\n", m, strerror(errno)); exit(2); } } while (0)

static pthread_mutex_t mtx_state = PTHREAD_MUTEX_INITIALIZER;  /* A: vehicle state */
static pthread_mutex_t mtx_frame = PTHREAD_MUTEX_INITIALIZER;  /* B: frame buffers */
static pthread_barrier_t freeze_bar;
static struct veh_state cur;
static atomic_int f_freeze, f_blank, f_stale, f_misdraw, f_crash, blank_arg;
static atomic_ullong run_id;
static atomic_int draw_tid;
static screen_context_t ctx;
static screen_window_t win;
static int evfd = -1;
static unsigned long frames;

static void bench_log(const char *cls, uint64_t t, int intended, int drawn)
{
    if (evfd < 0) return;
    char line[160];
    int n = snprintf(line, sizeof line, "%llu,%d,%s,%llu,%d,%d\n", (unsigned long long)atomic_load(&run_id), getpid(), cls,
                     (unsigned long long)t, intended, drawn);
    if (write(evfd, line, (size_t)n) < 0) perror("cluster: bench log");
}

static void *data_thread(void *arg)
{
    (void)arg;
    struct sched_param sp = {.sched_priority = 21};
    pthread_setschedparam(pthread_self(), SCHED_RR, &sp);
    pthread_setname_np(0, "data");
    int fd;
    while ((fd = open(PATH_VEHICLE, O_RDONLY)) < 0) usleep(50000);
    int chid = ChannelCreate(_NTO_CHF_PRIVATE), coid = ConnectAttach(0, 0, chid, _NTO_SIDE_CHANNEL, 0);
    struct sigevent ev;
    SIGEV_PULSE_INIT(&ev, coid, SIGEV_PULSE_PRIO_INHERIT, _PULSE_CODE_MINAVAIL, 0);
    SIGEV_MAKE_UPDATEABLE(&ev);
    if (MsgRegisterEvent(&ev, fd) == -1) perror("cluster: MsgRegisterEvent");
    int stale_logged = 0;
    for (;;) {
        if (ionotify(fd, _NOTIFY_ACTION_TRANARM, _NOTIFY_COND_INPUT, &ev) == -1) { perror("cluster: ionotify"); sleep(1); }
        struct veh_state s;
        if (devctl(fd, DCMD_VEH_GET, &s, sizeof s, NULL) == EOK) {
            if (atomic_load(&f_freeze)) {           /* lock-order inversion, data side: B then A */
                pthread_mutex_lock(&mtx_frame);
                pthread_barrier_wait(&freeze_bar);
                pthread_mutex_lock(&mtx_state);      /* blocks forever: the draw thread holds A, wants B */
            }
            if (atomic_load(&f_stale)) {
                if (!stale_logged) { bench_log("STALE", tt_now_ns(), disp_kmh(s.speed_kmh), disp_kmh(cur.speed_kmh)); stale_logged = 1; }
            } else {
                stale_logged = 0;
                pthread_mutex_lock(&mtx_state); cur = s; pthread_mutex_unlock(&mtx_state);
            }
        }
        struct _pulse p;
        MsgReceivePulse(chid, &p, sizeof p, NULL);
    }
    return NULL;
}

static void draw_frame(uint32_t *px, int st, const struct veh_state *s, int blank)
{
    tt_fill(px, st, L_WIN_W, L_WIN_H, PAL_GLASS);
    if (blank) return;
    int v = disp_kmh(s->speed_kmh), shown = v;
    if (atomic_load(&f_misdraw) && v >= 0) {      /* reverse the digits; a palindrome gets +11 */
        int r = 0, t = v; do { r = r * 10 + t % 10; t /= 10; } while (t);
        shown = (r == v) ? (v + 11) % 1000 : r;
    }
    int c[3];
    tt_cells_for(shown, c);
    for (int i = 0; i < L_DIGIT_N; i++)
        tt_blit_glyph(px + (long)L_DIGIT_Y * st + L_DIGIT_X0 + i * GLYPH_W, st, c[i], PAL_LIT, PAL_GLASS);
    for (int g = 0; g < 4; g++)
        tt_fill(px + (long)L_GEAR_Y * st + L_GEAR_X + g * L_GEAR_W + 4, st, L_GEAR_W - 8, L_GEAR_H,
                g == s->gear ? PAL_LIT : PAL_UNLIT);
    static const struct { int x; uint32_t c; } lamp[6] = {{80, PAL_GREEN}, {1136, PAL_GREEN}, {400, PAL_RED},
                                                         {520, PAL_AMBER}, {696, PAL_RED}, {816, PAL_BLUE}};
    for (int i = 0; i < 6; i++)
        tt_fill(px + (long)L_LAMP_Y * st + lamp[i].x, st, 64, 64, (s->lamps >> i) & 1 ? lamp[i].c : PAL_UNLIT);
}

static void *draw_thread(void *arg)
{
    (void)arg;
    struct sched_param sp = {.sched_priority = 20};
    pthread_setschedparam(pthread_self(), SCHED_RR, &sp);
    pthread_setname_np(0, "draw");
    atomic_store(&draw_tid, gettid());
    struct timespec next; clock_gettime(CLOCK_MONOTONIC, &next);
    int blank_logged = 0, mis_logged = 0, hidden = 0;
    for (;;) {
        if (atomic_load(&f_crash)) { bench_log("CRASH", tt_now_ns(), -1, -1); *(volatile int *)0 = 1; }
        pthread_mutex_lock(&mtx_state);
        if (atomic_load(&f_freeze)) {             /* draw side: holds A, waits for the data thread to hold B */
            pthread_barrier_wait(&freeze_bar);
            bench_log("FREEZE", tt_now_ns(), -1, -1);
            pthread_mutex_lock(&mtx_frame);       /* blocks forever */
        }
        struct veh_state s = cur;
        pthread_mutex_unlock(&mtx_state);

        int blank = atomic_load(&f_blank);
        if (blank && atomic_load(&blank_arg) == 1) {
            if (!hidden) { int vis = 0; bench_log("BLANK", tt_now_ns(), disp_kmh(s.speed_kmh), -1);
                           screen_set_window_property_iv(win, SCREEN_PROPERTY_VISIBLE, &vis); screen_flush_context(ctx, 0); hidden = 1; }
        } else if (hidden) { int vis = 1; screen_set_window_property_iv(win, SCREEN_PROPERTY_VISIBLE, &vis); hidden = 0; }

        pthread_mutex_lock(&mtx_frame);
        screen_buffer_t rb[2];
        CK(screen_get_window_property_pv(win, SCREEN_PROPERTY_RENDER_BUFFERS, (void **)rb), "render buffers");
        void *ptr; int stride;
        CK(screen_get_buffer_property_pv(rb[0], SCREEN_PROPERTY_POINTER, &ptr), "pointer");
        CK(screen_get_buffer_property_iv(rb[0], SCREEN_PROPERTY_STRIDE, &stride), "stride");
        uint32_t *px = ptr; int st = stride / 4;
        draw_frame(px, st, &s, blank);
        tt_fill(px + (long)L_TICK_Y * st + L_TICK_X, st, L_TICK_S, L_TICK_S,
                blank ? PAL_GLASS : PAL_TICK_BASE + (uint32_t)(frames & 0x0F));
        if (blank && !blank_logged && !hidden) { bench_log("BLANK", tt_now_ns(), disp_kmh(s.speed_kmh), -1); blank_logged = 1; }
        if (!blank) blank_logged = 0;
        int v = disp_kmh(s.speed_kmh);
        if (atomic_load(&f_misdraw) && !mis_logged) { bench_log("MISDRAW", tt_now_ns(), v, -2); mis_logged = 1; }
        if (!atomic_load(&f_misdraw)) mis_logged = 0;
        int r[4] = {0, 0, L_WIN_W, L_WIN_H};
        CK(screen_post_window(win, rb[0], 1, r, 0), "post");
        pthread_mutex_unlock(&mtx_frame);
        frames++;
        next.tv_nsec += 16666667;
        if (next.tv_nsec >= 1000000000) { next.tv_nsec -= 1000000000; next.tv_sec++; }
        struct timespec now; clock_gettime(CLOCK_MONOTONIC, &now);
        if (now.tv_sec > next.tv_sec || (now.tv_sec == next.tv_sec && now.tv_nsec > next.tv_nsec)) next = now; /* late: no catch-up burst */
        else clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &next, NULL);
    }
    return NULL;
}

static int io_devctl(resmgr_context_t *ctp, io_devctl_t *msg, iofunc_ocb_t *ocb)
{
    int st = iofunc_devctl_default(ctp, msg, ocb);
    if (st != _RESMGR_DEFAULT) return st;
    if (msg->i.dcmd != DCMD_CL_FAULT) return ENOSYS;
    struct cl_fault_cmd c;
    memcpy(&c, _DEVCTL_DATA(msg->i), sizeof c);
    atomic_store(&run_id, c.run_id);
    printf("cluster: fault command class=%d arg=%d run=%llu\n", c.cls, c.arg, (unsigned long long)c.run_id); fflush(stdout);
    switch (c.cls) {
    case CL_FREEZE:  atomic_store(&f_freeze, 1); break;
    case CL_BLANK:   atomic_store(&blank_arg, c.arg); atomic_store(&f_blank, 1); break;
    case CL_STALE:   atomic_store(&f_stale, 1); break;
    case CL_MISDRAW: atomic_store(&f_misdraw, 1); break;
    case CL_CRASH:   atomic_store(&f_crash, 1); break;
    case CL_HEAL:    atomic_store(&f_blank, 0); atomic_store(&f_stale, 0); atomic_store(&f_misdraw, 0); break;
    default: return EINVAL;
    }
    memset(&msg->o, 0, sizeof msg->o);
    return _RESMGR_PTR(ctp, &msg->o, sizeof msg->o);
}

static char g_wid[32];
static void *register_thread(void *arg)
{
    (void)arg;
    const char *wid = g_wid;
    struct tt_register r = {.pid = getpid(), .draw_tid = atomic_load(&draw_tid)};
    snprintf(r.win_id, sizeof r.win_id, "%s", wid);
    for (int tries = 0; tries < 600; tries++) {          /* the monitor may start after us: retry up to 60 s */
        int fd = open(PATH_TELLTALE, O_RDWR);
        if (fd >= 0) {
            int rc = devctl(fd, DCMD_TT_REGISTER, &r, sizeof r, NULL);
            close(fd);
            if (rc == EOK) { printf("cluster: registered with the monitor (pid %d, draw tid %d, window %s)\n", r.pid, r.draw_tid, wid); fflush(stdout); return NULL; }
            fprintf(stderr, "cluster: DCMD_TT_REGISTER: %s\n", strerror(rc));
        }
        usleep(100000);
    }
    fprintf(stderr, "cluster: no monitor to register with; running unmonitored\n");
    return NULL;
}

int main(void)
{
    tt_enter_keep_uid("cluster", SCHED_RR, 15);
    /* attach the test-hook path while still privileged, then drop to the renderer's uid */
    dispatch_t *dpp = dispatch_create();
    resmgr_attr_t rattr = {.nparts_max = 1, .msg_max_size = 1024};
    resmgr_connect_funcs_t cf; resmgr_io_funcs_t iof;
    iofunc_func_init(_RESMGR_CONNECT_NFUNCS, &cf, _RESMGR_IO_NFUNCS, &iof);
    iof.devctl = io_devctl;
    static iofunc_attr_t attr;
    iofunc_attr_init(&attr, S_IFCHR | 0666, NULL, NULL);
    if (resmgr_attach(dpp, &rattr, PATH_CLCTL, _FTYPE_ANY, 0, &cf, &iof, &attr) == -1) perror("cluster: attach " PATH_CLCTL);
    tt_drop_uid("cluster");
    const char *evdir = getenv("TT_EVDIR") ? getenv("TT_EVDIR") : "/tmp/tt/ev";
    char path[256]; snprintf(path, sizeof path, "%s/cluster-bench.csv", evdir);
    evfd = open(path, O_WRONLY | O_APPEND | O_CREAT, 0644);
    pthread_barrier_init(&freeze_bar, NULL, 2);

    CK(screen_create_context(&ctx, SCREEN_APPLICATION_CONTEXT), "create_context");
    CK(screen_create_window(&win, ctx), "create_window");
    int usage = SCREEN_USAGE_WRITE | SCREEN_USAGE_NATIVE, fmt = SCREEN_FORMAT_RGBA8888;
    int sz[2] = {L_WIN_W, L_WIN_H}, pos[2] = {L_WIN_X, L_WIN_Y}, z = 10;
    CK(screen_set_window_property_iv(win, SCREEN_PROPERTY_USAGE, &usage), "usage");
    CK(screen_set_window_property_iv(win, SCREEN_PROPERTY_FORMAT, &fmt), "format");
    CK(screen_set_window_property_iv(win, SCREEN_PROPERTY_BUFFER_SIZE, sz), "buffer size");
    CK(screen_set_window_property_iv(win, SCREEN_PROPERTY_SIZE, sz), "size");
    CK(screen_set_window_property_iv(win, SCREEN_PROPERTY_POSITION, pos), "position");
    CK(screen_set_window_property_iv(win, SCREEN_PROPERTY_ZORDER, &z), "zorder");
    char wid[32]; snprintf(wid, sizeof wid, "tt-cluster-%d", getpid());
    CK(screen_set_window_property_cv(win, SCREEN_PROPERTY_ID_STRING, (int)strlen(wid), wid), "id");
    int perm = 0;
    screen_get_window_property_iv(win, SCREEN_PROPERTY_PERMISSIONS, &perm);
    perm |= SCREEN_PERMISSION_IROTH | SCREEN_PERMISSION_IRGRP;   /* owner read grant: the monitor may read this window */
    if (screen_set_window_property_iv(win, SCREEN_PROPERTY_PERMISSIONS, &perm) != 0) perror("cluster: permissions");
    CK(screen_create_window_buffers(win, 2), "create buffers");
    screen_get_window_property_iv(win, SCREEN_PROPERTY_POSITION, pos);
    printf("cluster: window %s %dx%d at %d,%d z=%d perm=0x%x\n", wid, L_WIN_W, L_WIN_H, pos[0], pos[1], z, perm); fflush(stdout);

    pthread_t td, tw;
    pthread_create(&td, NULL, data_thread, NULL);
    pthread_create(&tw, NULL, draw_thread, NULL);
    while (!atomic_load(&draw_tid)) usleep(1000);

    snprintf(g_wid, sizeof g_wid, "%s", wid);
    pthread_t tr; pthread_create(&tr, NULL, register_thread, NULL);
    dispatch_context_t *dctp = dispatch_context_alloc(dpp);
    for (;;) { if ((dctp = dispatch_block(dctp))) dispatch_handler(dctp); }
}
