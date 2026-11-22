/* ttrec: record what Screen composes for the display (screen_read_display, display-manager context, root)
 * as a stream of changed tiles -- the footage in the demo video is these frames, read back on the target.
 *   ttrec <out.ttv> <period_ms> <seconds> [full_every x,y,w,h[@every] ...]       (start it low: on -p 9)
 * With no regions every frame reads the whole display. With regions (display coordinates, multiples of 32) frame 0
 * reads the whole display, then each region is read every <every> frames (default 1) and the whole display every
 * <full_every> frames: reading only where the cluster can change keeps the extra Screen work small (a whole-display
 * read costs ~40 ms of Screen time under QEMU TCG); its effect on the run being filmed was not measured.
 * At most 16 regions; a region must lie inside the display (x, y >= 0), or ttrec refuses to start.
 * File format (little-endian): "TTV1", u32 w, u32 h, u32 tile; then per frame: u64 t_ns (CLOCK_MONOTONIC,
 * the clock the Telltale logs use), u32 n, then n x { u16 tx, u16 ty, tile*tile*4 bytes BGRA }.
 * Frame 0 stores every tile; later frames only the tiles that changed. tools/ttv_decode.py turns it into PNGs.
 * A tile outside every region can lag by up to <full_every> frames (it is only refreshed by the whole-display read).
 * Stops after <seconds>, or on SIGTERM/SIGINT (slay's default signal; the file is complete up to the last frame).
 * Exit status: 0 = recording complete, 1 = bad arguments, 2 = setup failed, 3 = a read, write or the final close failed.
 * SPDX-License-Identifier: Apache-2.0 */
#include <errno.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <screen/screen.h>
#include "../common/ttsec.h"

#define TILE 32
#define MAXR 16

struct region { int r[4], every; screen_buffer_t buf; unsigned char *p; int stride; };

static volatile sig_atomic_t stop_req;
static void on_sig(int s) { (void)s; stop_req = 1; }

static int put_u32(FILE *f, uint32_t v) { return fwrite(&v, 4, 1, f) == 1 ? 0 : -1; }

static int make_region(screen_context_t ctx, struct region *g)
{
    screen_pixmap_t pix;
    if (screen_create_pixmap(&pix, ctx) != 0) return -1;
    int usage = SCREEN_USAGE_READ | SCREEN_USAGE_WRITE | SCREEN_USAGE_NATIVE, fmt = SCREEN_FORMAT_RGBA8888, sz[2] = {g->r[2], g->r[3]};
    screen_set_pixmap_property_iv(pix, SCREEN_PROPERTY_USAGE, &usage);
    screen_set_pixmap_property_iv(pix, SCREEN_PROPERTY_FORMAT, &fmt);
    screen_set_pixmap_property_iv(pix, SCREEN_PROPERTY_BUFFER_SIZE, sz);
    if (screen_create_pixmap_buffer(pix) != 0) return -1;
    screen_get_pixmap_property_pv(pix, SCREEN_PROPERTY_RENDER_BUFFERS, (void **)&g->buf);
    screen_get_buffer_property_pv(g->buf, SCREEN_PROPERTY_POINTER, (void **)&g->p);
    screen_get_buffer_property_iv(g->buf, SCREEN_PROPERTY_STRIDE, &g->stride);
    return 0;
}

int main(int argc, char **argv)
{
    if (argc < 4) { fprintf(stderr, "usage: ttrec <out.ttv> <period_ms> <seconds> [full_every x,y,w,h[@every] ...]\n"); return 1; }
    int period_ms = atoi(argv[2]), secs = atoi(argv[3]), full_every = argc > 4 ? atoi(argv[4]) : 1;
    if (period_ms < 1 || secs < 1 || full_every < 1) { fprintf(stderr, "ttrec: period_ms, seconds and full_every must be >= 1\n"); return 1; }
    screen_context_t ctx;
    if (screen_create_context(&ctx, SCREEN_DISPLAY_MANAGER_CONTEXT) != 0) { perror("ttrec: display-manager context (root)"); return 2; }
    int n = 0; screen_get_context_property_iv(ctx, SCREEN_PROPERTY_DISPLAY_COUNT, &n);
    if (n < 1) { fprintf(stderr, "ttrec: no display\n"); return 2; }
    screen_display_t *d = calloc((size_t)n, sizeof *d);
    if (!d) { fprintf(stderr, "ttrec: out of memory\n"); return 2; }
    screen_get_context_property_pv(ctx, SCREEN_PROPERTY_DISPLAYS, (void **)d);
    int dsz[2]; screen_get_display_property_iv(d[0], SCREEN_PROPERTY_SIZE, dsz);
    if (dsz[0] % TILE || dsz[1] % TILE) { fprintf(stderr, "ttrec: display %dx%d is not a multiple of %d\n", dsz[0], dsz[1], TILE); return 2; }

    struct region g[MAXR + 1] = {{{0, 0, dsz[0], dsz[1]}, full_every, 0, 0, 0}};   /* g[0] = the whole display */
    int ng = 1;
    if (argc - 5 > MAXR) { fprintf(stderr, "ttrec: at most %d regions (got %d)\n", MAXR, argc - 5); return 1; }
    for (int i = 5; i < argc; i++, ng++) {
        struct region *q = &g[ng]; q->every = 1;
        if (sscanf(argv[i], "%d,%d,%d,%d@%d", &q->r[0], &q->r[1], &q->r[2], &q->r[3], &q->every) < 4 || q->every < 1 ||
            q->r[0] < 0 || q->r[1] < 0 || q->r[0] % TILE || q->r[1] % TILE || q->r[2] % TILE || q->r[3] % TILE || q->r[2] < TILE || q->r[3] < TILE ||
            q->r[0] + q->r[2] > dsz[0] || q->r[1] + q->r[3] > dsz[1]) {
            fprintf(stderr, "ttrec: bad region %s (x,y,w,h[@every], multiples of %d, inside %dx%d)\n", argv[i], TILE, dsz[0], dsz[1]);
            return 1;
        }
    }
    if (ng == 1) g[0].every = 1;                 /* no regions: the whole display every frame */
    for (int i = 0; i < ng; i++) if (make_region(ctx, &g[i]) != 0) { perror("ttrec: pixmap"); return 2; }

    const int W = dsz[0], tw = W / TILE, th = dsz[1] / TILE, row = TILE * 4;
    unsigned char *prev = calloc((size_t)W * (size_t)dsz[1], 4);              /* last stored frame, packed rows */
    unsigned char *seen = calloc((size_t)tw * (size_t)th, 1);                 /* tile already stored this frame */
    unsigned char *out = malloc((size_t)tw * (size_t)th * (4 + (size_t)TILE * row));
    if (!prev || !seen || !out) { fprintf(stderr, "ttrec: out of memory\n"); return 2; }
    FILE *f = fopen(argv[1], "wb");
    if (!f) { perror(argv[1]); return 2; }
    setvbuf(f, NULL, _IOFBF, 1 << 20);
    fwrite("TTV1", 1, 4, f);
    if (put_u32(f, (uint32_t)W) || put_u32(f, (uint32_t)dsz[1]) || put_u32(f, TILE)) { perror("ttrec: write"); return 2; }
    signal(SIGTERM, on_sig); signal(SIGINT, on_sig);

    struct timespec next; clock_gettime(CLOCK_MONOTONIC, &next);
    const uint64_t t_end = tt_now_ns() + (uint64_t)secs * 1000000000ull;
    unsigned long frames = 0, tiles = 0, late = 0, reads = 0;
    int err = 0;
    while (!stop_req && !err) {
        uint64_t t = tt_now_ns();
        if (t >= t_end) break;
        size_t o = 0; uint32_t cnt = 0;
        memset(seen, 0, (size_t)tw * (size_t)th);
        for (int k = 0; k < ng && !err; k++) {
            struct region *q = &g[k];
            if (frames && frames % (unsigned long)q->every) continue;
            if (screen_read_display(d[0], q->buf, 1, q->r, 0) != 0) {
                fprintf(stderr, "ttrec: screen_read_display: %s\n", strerror(errno)); err = 1; break;
            }
            reads++;
            for (int j = 0; j < q->r[3] / TILE; j++)
                for (int i = 0; i < q->r[2] / TILE; i++) {
                    int tx = q->r[0] / TILE + i, ty = q->r[1] / TILE + j;
                    if (seen[ty * tw + tx]) continue;
                    int changed = frames == 0;
                    for (int y = 0; y < TILE && !changed; y++)
                        changed = memcmp(q->p + (size_t)(j * TILE + y) * (size_t)q->stride + (size_t)i * row,
                                         prev + ((size_t)(ty * TILE + y) * (size_t)W + (size_t)tx * TILE) * 4, (size_t)row) != 0;
                    if (!changed) continue;
                    seen[ty * tw + tx] = 1;
                    uint16_t xy[2] = {(uint16_t)tx, (uint16_t)ty};
                    memcpy(out + o, xy, 4); o += 4;
                    for (int y = 0; y < TILE; y++) {
                        const unsigned char *src = q->p + (size_t)(j * TILE + y) * (size_t)q->stride + (size_t)i * row;
                        memcpy(out + o, src, (size_t)row); o += (size_t)row;
                        memcpy(prev + ((size_t)(ty * TILE + y) * (size_t)W + (size_t)tx * TILE) * 4, src, (size_t)row);
                    }
                    cnt++;
                }
        }
        if (err) break;
        if (fwrite(&t, 8, 1, f) != 1 || put_u32(f, cnt) || (o && fwrite(out, 1, o, f) != o)) { perror("ttrec: write"); err = 1; break; }
        frames++; tiles += cnt;
        next.tv_nsec += (long)period_ms * 1000000L;
        while (next.tv_nsec >= 1000000000L) { next.tv_nsec -= 1000000000L; next.tv_sec++; }
        struct timespec now; clock_gettime(CLOCK_MONOTONIC, &now);
        if (now.tv_sec > next.tv_sec || (now.tv_sec == next.tv_sec && now.tv_nsec >= next.tv_nsec)) { late++; next = now; }
        else clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &next, NULL);
    }
    if (fclose(f) != 0) { perror("ttrec: close"); err = 1; }   /* the buffered tail is written here: a full RAM disk shows up now */
    printf("ttrec: %s display %dx%d, %d region(s) + whole display every %d, %lu frames, %lu reads, %lu tiles, %lu late (period %d ms)\n",
           argv[1], W, dsz[1], ng - 1, g[0].every, frames, reads, tiles, late, period_ms);
    return err ? 3 : 0;
}
