/* SPDX-License-Identifier: Apache-2.0 */
#include <stdlib.h>
#include <string.h>
#include "decode.h"
#include "glyphs.h"
#include "palette.h"
#include "layout.h"

/* a pixel is "ink" when its mean channel value is above the glass/lit midpoint */
static inline int ink(uint32_t p)
{
    unsigned r = (p >> 16) & 0xFF, g = (p >> 8) & 0xFF, b = p & 0xFF;
    return (r + g + b) > 3u * 0x80u;
}

int tt_decode_cell(const uint32_t *base, int stride_px, int h_max, int *dist)
{
    uint8_t bits[GLYPH_H * GLYPH_ROW_BYTES];
    for (int y = 0; y < GLYPH_H; y++) {
        const uint32_t *row = base + (long)y * stride_px;
        uint8_t *o = bits + y * GLYPH_ROW_BYTES;
        for (int b = 0; b < GLYPH_ROW_BYTES; b++) {
            uint8_t v = 0;
            for (int k = 0; k < 8; k++) {
                int x = b * 8 + k;
                v = (uint8_t)((v << 1) | (x < GLYPH_W ? ink(row[x]) : 0));
            }
            o[b] = v;
        }
    }
    /* same row-padded layout as the atlas, so the distance is XOR + popcount over whole 64-bit words */
    enum { NB = GLYPH_H * GLYPH_ROW_BYTES, NW = NB / 8 };
    _Static_assert(NB % 8 == 0, "atlas size must be a multiple of 8 bytes");
    uint64_t w[NW];
    memcpy(w, bits, NB);
    int best = DEC_UNREADABLE, bestd = 1 << 30;
    for (int g = 0; g < GLYPH_N; g++) {
        int d = 0;
        const uint8_t *gb = glyph_bits[g];
        for (int i = 0; i < NW && d < bestd; i++) {
            uint64_t a; memcpy(&a, gb + i * 8, 8);
            d += __builtin_popcountll(w[i] ^ a);
        }
        if (d < bestd) { bestd = d; best = g; }
    }
    if (dist) *dist = bestd;
    return bestd <= h_max ? best : DEC_UNREADABLE;
}

enum speed_kind tt_combine_cells(const int c[3], int *value)
{
    *value = -1;
    for (int i = 0; i < 3; i++) if (c[i] == DEC_UNREADABLE) return SPD_UNREADABLE;
    if (c[0] == GLYPH_BLANK && c[1] == GLYPH_BLANK && c[2] == GLYPH_BLANK) return SPD_BLANK;
    if (c[0] == GLYPH_BLANK && c[1] == GLYPH_DASH && c[2] == GLYPH_DASH) return SPD_DASH;
    int v = 0, started = 0;
    for (int i = 0; i < 3; i++) {
        if (c[i] == GLYPH_BLANK) { if (started) return SPD_UNREADABLE; continue; }
        if (c[i] > 9) return SPD_UNREADABLE;                 /* stray dash */
        if (!started && c[i] == 0 && i < 2) return SPD_UNREADABLE; /* leading zero is never drawn */
        started = 1; v = v * 10 + c[i];
    }
    if (!started) return SPD_UNREADABLE;
    *value = v;
    return SPD_VALUE;
}

void tt_cells_for(int v, int c[3])
{
    c[0] = c[1] = c[2] = GLYPH_BLANK;
    if (v < 0 || v > 999) { c[1] = c[2] = GLYPH_DASH; return; }
    c[2] = v % 10;
    if (v >= 10) c[1] = (v / 10) % 10;
    if (v >= 100) c[0] = v / 100;
}

int tt_tick_value(const uint32_t *base, int stride_px)
{
    uint32_t p0 = base[0] & 0xFFFFFFu;
    if ((p0 & 0xFFFF00u) != (PAL_TICK_BASE & 0xFFFF00u)) return -1;
    int v = (int)(p0 & 0xFF) - 0x08;
    if (v < 0 || v > 15) return -1;
    for (int y = 0; y < 8; y++)
        for (int x = 0; x < 8; x++)
            if ((base[(long)y * stride_px + x] & 0xFFFFFFu) != p0) return -1;
    return v;
}

uint32_t tt_marker_px(int x, int y) { return ((x / 2 + y / 2) & 1) ? PAL_RED : PAL_LIT; }

int tt_marker_ok(const uint32_t *base, int stride_px)
{
    for (int y = 0; y < 8; y++)
        for (int x = 0; x < 8; x++)
            if ((base[(long)y * stride_px + x] & 0xFFFFFFu) != tt_marker_px(x, y)) return 0;
    return 1;
}

void tt_blit_glyph(uint32_t *base, int stride_px, int g, uint32_t fg, uint32_t bg)
{
    fg |= 0xFF000000u; bg |= 0xFF000000u;
    for (int y = 0; y < GLYPH_H; y++) {
        const uint8_t *gr = glyph_bits[g] + y * GLYPH_ROW_BYTES;
        uint32_t *row = base + (long)y * stride_px;
        for (int x = 0; x < GLYPH_W; x++)
            row[x] = (gr[x >> 3] & (0x80 >> (x & 7))) ? fg : bg;
    }
}

void tt_fill(uint32_t *base, int stride_px, int w, int h, uint32_t rgb)
{
    rgb |= 0xFF000000u;
    for (int y = 0; y < h; y++) {
        uint32_t *row = base + (long)y * stride_px;
        for (int x = 0; x < w; x++) row[x] = rgb;
    }
}

int tt_lamp_permille(const uint32_t *base, int stride_px, int w, int h, uint32_t rgb, int tol)
{
    long hit = 0;
    int r = (rgb >> 16) & 0xFF, g = (rgb >> 8) & 0xFF, b = rgb & 0xFF;
    for (int y = 0; y < h; y++)
        for (int x = 0; x < w; x++) {
            uint32_t p = base[(long)y * stride_px + x];
            int pr = (p >> 16) & 0xFF, pg = (p >> 8) & 0xFF, pb = p & 0xFF;
            if (abs(pr - r) <= tol && abs(pg - g) <= tol && abs(pb - b) <= tol) hit++;
        }
    return w > 0 && h > 0 ? (int)(hit * 1000 / ((long)w * h)) : 0;
}

unsigned tt_lamps_decode(const uint32_t *row, int stride_px, int thr_permille)
{
    unsigned bits = 0;
    for (int i = 0; i < L_LAMP_N; i++)
        if (tt_lamp_permille(row + L_LAMP[i].x, stride_px, L_LAMP_S, L_LAMP_S, L_LAMP[i].rgb, 24) >= thr_permille) bits |= 1u << i;
    return bits;
}
