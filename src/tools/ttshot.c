/* ttshot: capture what Screen composed for the display (screen_read_display, display-manager context, root)
 * into a binary PPM -- evidence frames and the host-vs-target comparison.
 *   ttshot <out.ppm> [x y w h]
 * SPDX-License-Identifier: Apache-2.0 */
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <screen/screen.h>
#include "../common/ttsec.h"

int main(int argc, char **argv)
{
    if (argc < 2) { fprintf(stderr, "usage: ttshot <out.ppm> [x y w h]\n"); return 1; }
    screen_context_t ctx;
    if (screen_create_context(&ctx, SCREEN_DISPLAY_MANAGER_CONTEXT) != 0) { perror("ttshot: display-manager context (root)"); return 2; }
    int n = 0; screen_get_context_property_iv(ctx, SCREEN_PROPERTY_DISPLAY_COUNT, &n);
    if (n < 1) { fprintf(stderr, "ttshot: no display\n"); return 2; }
    screen_display_t *d = calloc((size_t)n, sizeof *d);
    screen_get_context_property_pv(ctx, SCREEN_PROPERTY_DISPLAYS, (void **)d);
    int dsz[2]; screen_get_display_property_iv(d[0], SCREEN_PROPERTY_SIZE, dsz);
    int r[4] = {0, 0, dsz[0], dsz[1]};
    if (argc >= 6) for (int i = 0; i < 4; i++) r[i] = atoi(argv[2 + i]);
    screen_pixmap_t pix; screen_create_pixmap(&pix, ctx);
    int usage = SCREEN_USAGE_READ | SCREEN_USAGE_WRITE | SCREEN_USAGE_NATIVE, fmt = SCREEN_FORMAT_RGBA8888, sz[2] = {r[2], r[3]};
    screen_set_pixmap_property_iv(pix, SCREEN_PROPERTY_USAGE, &usage);
    screen_set_pixmap_property_iv(pix, SCREEN_PROPERTY_FORMAT, &fmt);
    screen_set_pixmap_property_iv(pix, SCREEN_PROPERTY_BUFFER_SIZE, sz);
    if (screen_create_pixmap_buffer(pix) != 0) { perror("ttshot: pixmap"); return 2; }
    screen_buffer_t buf; screen_get_pixmap_property_pv(pix, SCREEN_PROPERTY_RENDER_BUFFERS, (void **)&buf);
    void *p; int stride;
    screen_get_buffer_property_pv(buf, SCREEN_PROPERTY_POINTER, &p);
    screen_get_buffer_property_iv(buf, SCREEN_PROPERTY_STRIDE, &stride);
    uint64_t t = tt_now_ns();
    if (screen_read_display(d[0], buf, 1, r, 0) != 0) { fprintf(stderr, "ttshot: screen_read_display: %s\n", strerror(errno)); return 3; }
    FILE *f = fopen(argv[1], "wb");
    if (!f) { perror(argv[1]); return 2; }
    fprintf(f, "P6\n%d %d\n255\n", r[2], r[3]);
    for (int y = 0; y < r[3]; y++)
        for (int x = 0; x < r[2]; x++) {
            uint32_t v = ((uint32_t *)((char *)p + (size_t)y * stride))[x];
            unsigned char rgb[3] = {(v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF};
            fwrite(rgb, 1, 3, f);
        }
    fclose(f);
    printf("ttshot: %s %dx%d+%d+%d of display %dx%d at t_ns=%llu\n", argv[1], r[2], r[3], r[0], r[1], dsz[0], dsz[1], (unsigned long long)t);
    return 0;
}
