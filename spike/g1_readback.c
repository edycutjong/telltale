/* G1 spike: Screen read-back of displayed pixels (QNX 8.0).
 * modes:
 *   g1 render X Y        app context; 400x200 window at (X,Y), z=10, known pattern; stays up
 *   g1 cover X Y W H     app context; solid magenta window, z=20; stays up
 *   g1 read X Y [tag]    privileged (WM|DM, root); route 1 screen_read_display of the renderer rect
 *                        and route 2 screen_read_window of the renderer window; compares both with the
 *                        expected pattern; writes PPMs; prints one RESULT line per route
 *   g1 bench N           privileged; N checks x 3 screen_read_display calls (lamp row 1280x64,
 *                        digit block 360x336, tick 8x8); prints per-check ns (CSV) to stdout
 */
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <screen/screen.h>

#define PW 400
#define PH 200
#define DIE(m) do { fprintf(stderr, "FAIL %s: %s (errno %d) line %d\n", m, strerror(errno), errno, __LINE__); exit(2); } while (0)
#define CK(x, m) do { if ((x) != 0) DIE(m); } while (0)

/* expected pattern, 0x00RRGGBB (alpha checked separately) */
static uint32_t pat(int x, int y)
{
    if (y < 100 && x < 160) { /* four primaries, 40 px each */
        static const uint32_t c[4] = {0xFF0000, 0x00FF00, 0x0000FF, 0xFFFFFF};
        return c[x / 40];
    }
    if (x >= 170 && x < 270 && y >= 10 && y < 190) { /* digit cell: a 7-seg "4" in white on black */
        int lx = x - 170, ly = y - 10;
        int left = lx < 16 && ly < 98, right = lx >= 84, mid = ly >= 82 && ly < 98;
        return (left || right || mid) ? 0xFFFFFF : 0x000000;
    }
    if (y >= 120 && y < 128 && x >= 280 && x < 280 + 16 * 8) /* tick ladder: blue steps of 1 */
        return 0x000080 + (uint32_t)((x - 280) / 8);
    return 0x101010;
}

static void fill(void *ptr, int stride, int w, int h, uint32_t solid, int use_pat)
{
    for (int y = 0; y < h; y++) {
        uint32_t *row = (uint32_t *)((char *)ptr + (size_t)y * stride);
        for (int x = 0; x < w; x++)
            row[x] = 0xFF000000u | (use_pat ? pat(x, y) : solid);
    }
}

static screen_window_t mkwin(screen_context_t ctx, int x, int y, int w, int h, int z, const char *id, void **ptr, int *stride, screen_buffer_t *buf)
{
    screen_window_t win;
    CK(screen_create_window(&win, ctx), "create_window");
    int usage = SCREEN_USAGE_WRITE | SCREEN_USAGE_NATIVE, fmt = SCREEN_FORMAT_RGBA8888;
    int sz[2] = {w, h}, pos[2] = {x, y};
    CK(screen_set_window_property_iv(win, SCREEN_PROPERTY_USAGE, &usage), "usage");
    CK(screen_set_window_property_iv(win, SCREEN_PROPERTY_FORMAT, &fmt), "format");
    CK(screen_set_window_property_iv(win, SCREEN_PROPERTY_BUFFER_SIZE, sz), "bufsize");
    CK(screen_set_window_property_iv(win, SCREEN_PROPERTY_SIZE, sz), "size");
    CK(screen_set_window_property_iv(win, SCREEN_PROPERTY_POSITION, pos), "pos");
    CK(screen_set_window_property_iv(win, SCREEN_PROPERTY_ZORDER, &z), "zorder");
    CK(screen_set_window_property_cv(win, SCREEN_PROPERTY_ID_STRING, (int)strlen(id), id), "id");
    int perm = 0;
    CK(screen_get_window_property_iv(win, SCREEN_PROPERTY_PERMISSIONS, &perm), "getperm");
    perm |= SCREEN_PERMISSION_IROTH | SCREEN_PERMISSION_IRGRP; /* owner read grant for S-UID */
    if (screen_set_window_property_iv(win, SCREEN_PROPERTY_PERMISSIONS, &perm) != 0)
        fprintf(stderr, "warn: set PERMISSIONS: %s\n", strerror(errno));
    CK(screen_create_window_buffers(win, 1), "create_buffers");
    CK(screen_get_window_property_pv(win, SCREEN_PROPERTY_RENDER_BUFFERS, (void **)buf), "render_buffers");
    CK(screen_get_buffer_property_pv(*buf, SCREEN_PROPERTY_POINTER, ptr), "pointer");
    CK(screen_get_buffer_property_iv(*buf, SCREEN_PROPERTY_STRIDE, stride), "stride");
    return win;
}

static screen_pixmap_t mkpix(screen_context_t ctx, int w, int h, void **ptr, int *stride, screen_buffer_t *buf)
{
    screen_pixmap_t pix;
    CK(screen_create_pixmap(&pix, ctx), "create_pixmap");
    int usage = SCREEN_USAGE_READ | SCREEN_USAGE_WRITE | SCREEN_USAGE_NATIVE, fmt = SCREEN_FORMAT_RGBA8888, sz[2] = {w, h};
    CK(screen_set_pixmap_property_iv(pix, SCREEN_PROPERTY_USAGE, &usage), "pix usage");
    CK(screen_set_pixmap_property_iv(pix, SCREEN_PROPERTY_FORMAT, &fmt), "pix format");
    CK(screen_set_pixmap_property_iv(pix, SCREEN_PROPERTY_BUFFER_SIZE, sz), "pix size");
    CK(screen_create_pixmap_buffer(pix), "pix buffer");
    CK(screen_get_pixmap_property_pv(pix, SCREEN_PROPERTY_RENDER_BUFFERS, (void **)buf), "pix rbuf");
    CK(screen_get_buffer_property_pv(*buf, SCREEN_PROPERTY_POINTER, ptr), "pix ptr");
    CK(screen_get_buffer_property_iv(*buf, SCREEN_PROPERTY_STRIDE, stride), "pix stride");
    return pix;
}

static void ppm(const char *path, void *ptr, int stride, int w, int h)
{
    FILE *f = fopen(path, "wb");
    if (!f) { perror(path); return; }
    fprintf(f, "P6\n%d %d\n255\n", w, h);
    for (int y = 0; y < h; y++)
        for (int x = 0; x < w; x++) {
            uint32_t p = ((uint32_t *)((char *)ptr + (size_t)y * stride))[x];
            unsigned char rgb[3] = {(p >> 16) & 0xFF, (p >> 8) & 0xFF, p & 0xFF};
            fwrite(rgb, 1, 3, f);
        }
    fclose(f);
}

/* compare a read-back against the pattern; returns mismatching pixels, counts alpha values != 0xFF */
static long cmp(void *ptr, int stride, int w, int h, long *alpha_off, long *first)
{
    long bad = 0; *alpha_off = 0; *first = -1;
    for (int y = 0; y < h; y++)
        for (int x = 0; x < w; x++) {
            uint32_t p = ((uint32_t *)((char *)ptr + (size_t)y * stride))[x];
            if ((p >> 24) != 0xFF) (*alpha_off)++;
            if ((p & 0xFFFFFF) != pat(x, y)) { if (*first < 0) *first = (long)y * w + x; bad++; }
        }
    return bad;
}

static long long now_ns(void)
{
    struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t);
    return (long long)t.tv_sec * 1000000000LL + t.tv_nsec;
}

static screen_display_t first_display(screen_context_t ctx, int *dsz)
{
    int n = 0;
    CK(screen_get_context_property_iv(ctx, SCREEN_PROPERTY_DISPLAY_COUNT, &n), "display_count");
    if (n < 1) DIE("no display");
    screen_display_t *d = calloc((size_t)n, sizeof *d);
    CK(screen_get_context_property_pv(ctx, SCREEN_PROPERTY_DISPLAYS, (void **)d), "displays");
    CK(screen_get_display_property_iv(d[0], SCREEN_PROPERTY_SIZE, dsz), "display size");
    int mirror = -1;
    screen_get_display_property_iv(d[0], SCREEN_PROPERTY_MIRROR_MODE, &mirror);
    char idstr[64] = "";
    screen_get_display_property_cv(d[0], SCREEN_PROPERTY_ID_STRING, sizeof idstr, idstr);
    fprintf(stderr, "display count=%d id='%s' size=%dx%d mirror_mode=%d\n", n, idstr, dsz[0], dsz[1], mirror);
    return d[0];
}

static screen_window_t find_window(screen_context_t ctx, const char *want)
{
    for (int tries = 0; tries < 50; tries++) {
        int n = 0;
        screen_get_context_property_iv(ctx, SCREEN_PROPERTY_WINDOW_COUNT, &n);
        if (n > 0) {
            screen_window_t *w = calloc((size_t)n, sizeof *w);
            if (screen_get_context_property_pv(ctx, SCREEN_PROPERTY_WINDOWS, (void **)w) == 0)
                for (int i = 0; i < n; i++) {
                    char id[64] = "";
                    screen_get_window_property_cv(w[i], SCREEN_PROPERTY_ID_STRING, sizeof id, id);
                    if (strcmp(id, want) == 0) { fprintf(stderr, "found window '%s' via WINDOWS list (n=%d)\n", want, n); return w[i]; }
                }
        }
        /* drain events: a WM context gets CREATE events with window handles */
        screen_event_t ev; screen_create_event(&ev);
        while (screen_get_event(ctx, ev, 20000000) == 0) {
            int type = SCREEN_EVENT_NONE;
            screen_get_event_property_iv(ev, SCREEN_PROPERTY_TYPE, &type);
            if (type == SCREEN_EVENT_NONE) break;
            if (type == SCREEN_EVENT_CREATE) {
                screen_window_t win;
                if (screen_get_event_property_pv(ev, SCREEN_PROPERTY_WINDOW, (void **)&win) == 0) {
                    char id[64] = "";
                    screen_get_window_property_cv(win, SCREEN_PROPERTY_ID_STRING, sizeof id, id);
                    if (strcmp(id, want) == 0) { fprintf(stderr, "found window '%s' via CREATE event\n", want); return win; }
                }
            }
        }
        screen_destroy_event(ev);
    }
    return NULL;
}

int main(int argc, char **argv)
{
    if (argc < 2) { fprintf(stderr, "usage: see source\n"); return 1; }
    screen_context_t ctx;
    const char *mode = argv[1];

    if (!strcmp(mode, "render") || !strcmp(mode, "cover")) {
        int cover = !strcmp(mode, "cover");
        int x = argc > 2 ? atoi(argv[2]) : 0, y = argc > 3 ? atoi(argv[3]) : 0;
        int w = cover && argc > 4 ? atoi(argv[4]) : PW, h = cover && argc > 5 ? atoi(argv[5]) : PH;
        CK(screen_create_context(&ctx, SCREEN_APPLICATION_CONTEXT), "create_context(app)");
        void *ptr; int stride; screen_buffer_t buf;
        screen_window_t win = mkwin(ctx, x, y, w, h, cover ? 20 : 10, cover ? "tt-cover" : "tt-render", &ptr, &stride, &buf);
        fill(ptr, stride, w, h, 0xFF00FF, !cover);
        int r[4] = {0, 0, w, h};
        CK(screen_post_window(win, buf, 1, r, 0), "post_window");
        int pos[2], z, sz[2], perm;
        screen_get_window_property_iv(win, SCREEN_PROPERTY_POSITION, pos);
        screen_get_window_property_iv(win, SCREEN_PROPERTY_ZORDER, &z);
        screen_get_window_property_iv(win, SCREEN_PROPERTY_SIZE, sz);
        screen_get_window_property_iv(win, SCREEN_PROPERTY_PERMISSIONS, &perm);
        printf("%s up: pid=%d uid=%d pos=%d,%d size=%dx%d z=%d stride=%d perm=0x%x\n", mode, getpid(), getuid(), pos[0], pos[1], sz[0], sz[1], z, stride, perm);
        fflush(stdout);
        for (;;) pause();
    }

    if (!strcmp(mode, "read")) {
        int x = argc > 2 ? atoi(argv[2]) : 0, y = argc > 3 ? atoi(argv[3]) : 0;
        const char *tag = argc > 4 ? argv[4] : "plain";
        int ctype = SCREEN_WINDOW_MANAGER_CONTEXT | SCREEN_DISPLAY_MANAGER_CONTEXT;
        if (getenv("TT_CTX_APP")) ctype = SCREEN_APPLICATION_CONTEXT;
        if (screen_create_context(&ctx, ctype) != 0) {
            printf("RESULT route=ctx tag=%s uid=%d status=FAIL errno=%d (%s)\n", tag, getuid(), errno, strerror(errno));
            return 3;
        }
        int dsz[2] = {0, 0};
        screen_display_t disp = first_display(ctx, dsz);
        char path[128];

        /* route 1: display read of the renderer rect, rect-sized pixmap */
        void *p1; int s1; screen_buffer_t b1;
        mkpix(ctx, PW, PH, &p1, &s1, &b1);
        memset(p1, 0, (size_t)s1 * PH);
        int rect[4] = {x, y, PW, PH};
        long long t0 = now_ns();
        int rc = screen_read_display(disp, b1, 1, rect, 0);
        long long t1 = now_ns();
        int e = errno;
        if (rc != 0) {
            printf("RESULT route=display tag=%s uid=%d status=FAIL errno=%d (%s)\n", tag, getuid(), e, strerror(e));
        } else {
            long aoff, first; long bad = cmp(p1, s1, PW, PH, &aoff, &first);
            snprintf(path, sizeof path, "g1-display-%s.ppm", tag); ppm(path, p1, s1, PW, PH);
            printf("RESULT route=display tag=%s uid=%d status=%s mismatch_px=%ld of %d first_bad=%ld alpha_not_ff=%ld read_ns=%lld pix_stride=%d fmt=RGBA8888 rect=%d,%d,%d,%d\n",
                   tag, getuid(), bad ? "DIFF" : "EXACT", bad, PW * PH, first, aoff, t1 - t0, s1, x, y, PW, PH);
        }
        /* full display capture for the record */
        void *pf; int sf; screen_buffer_t bf;
        mkpix(ctx, dsz[0], dsz[1], &pf, &sf, &bf);
        if (screen_read_display(disp, bf, 0, NULL, 0) == 0) {
            snprintf(path, sizeof path, "g1-fulldisplay-%s.ppm", tag); ppm(path, pf, sf, dsz[0], dsz[1]);
        } else fprintf(stderr, "full display read failed: %s\n", strerror(errno));

        /* route 2: window read of the renderer window */
        screen_window_t rw = find_window(ctx, "tt-render");
        if (!rw) {
            printf("RESULT route=window tag=%s uid=%d status=FAIL reason=window-not-found\n", tag, getuid());
        } else {
            void *p2; int s2; screen_buffer_t b2;
            mkpix(ctx, PW, PH, &p2, &s2, &b2);
            memset(p2, 0, (size_t)s2 * PH);
            t0 = now_ns();
            rc = screen_read_window(rw, b2, 0, NULL, 0);
            t1 = now_ns();
            e = errno;
            if (rc != 0) {
                printf("RESULT route=window tag=%s uid=%d status=FAIL errno=%d (%s)\n", tag, getuid(), e, strerror(e));
            } else {
                long aoff, first; long bad = cmp(p2, s2, PW, PH, &aoff, &first);
                snprintf(path, sizeof path, "g1-window-%s.ppm", tag); ppm(path, p2, s2, PW, PH);
                int wpos[2] = {0, 0}, wz = 0;
                screen_get_window_property_iv(rw, SCREEN_PROPERTY_POSITION, wpos);
                screen_get_window_property_iv(rw, SCREEN_PROPERTY_ZORDER, &wz);
                printf("RESULT route=window tag=%s uid=%d status=%s mismatch_px=%ld of %d first_bad=%ld alpha_not_ff=%ld read_ns=%lld win_pos=%d,%d win_z=%d\n",
                       tag, getuid(), bad ? "DIFF" : "EXACT", bad, PW * PH, first, aoff, t1 - t0, wpos[0], wpos[1], wz);
            }
        }
        return 0;
    }

    if (!strcmp(mode, "bench")) {
        int n = argc > 2 ? atoi(argv[2]) : 500;
        CK(screen_create_context(&ctx, SCREEN_DISPLAY_MANAGER_CONTEXT), "create_context(dm)");
        int dsz[2]; screen_display_t disp = first_display(ctx, dsz);
        static const int R[3][4] = {{0, 0, 1280, 64}, {460, 200, 360, 336}, {1000, 600, 8, 8}};
        screen_buffer_t b[3]; void *p; int s;
        for (int i = 0; i < 3; i++) mkpix(ctx, R[i][2], R[i][3], &p, &s, &b[i]);
        printf("i,check_ns,lamp_ns,digit_ns,tick_ns\n");
        for (int i = 0; i < n; i++) {
            long long t[4];
            t[0] = now_ns();
            for (int k = 0; k < 3; k++) {
                if (screen_read_display(disp, b[k], 1, R[k], 0) != 0) DIE("read_display");
                t[k + 1] = now_ns();
            }
            printf("%d,%lld,%lld,%lld,%lld\n", i, t[3] - t[0], t[1] - t[0], t[2] - t[1], t[3] - t[2]);
            usleep(20000);
        }
        return 0;
    }
    fprintf(stderr, "unknown mode\n");
    return 1;
}
