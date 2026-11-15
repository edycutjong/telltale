/* Host tests: glyph decode, cell combine, tick, marker, truth window.  SPDX-License-Identifier: Apache-2.0 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "../../src/common/decode.h"
#include "../../src/common/glyphs.h"
#include "../../src/common/palette.h"
#include "../../src/common/truth.h"
#include "../../src/common/layout.h"
#include "../../src/common/disp.h"

static int fails, runs;
#define T(c, msg) do { runs++; if (!(c)) { fails++; printf("FAIL %s (line %d)\n", msg, __LINE__); } } while (0)

#define STRIDE 160
static uint32_t cell[GLYPH_H * STRIDE];

static void flip(int n, unsigned seed)
{
    srand(seed);
    for (int i = 0; i < n; i++) {
        int x = rand() % GLYPH_W, y = rand() % GLYPH_H;
        uint32_t *p = &cell[y * STRIDE + x];
        *p = ((*p & 0xFFFFFF) == PAL_LIT) ? (0xFF000000u | PAL_GLASS) : (0xFF000000u | PAL_LIT);
    }
}

int main(void)
{
    int d;
    for (int g = 0; g < GLYPH_N; g++) {   /* every glyph decodes to itself at distance 0 */
        tt_blit_glyph(cell, STRIDE, g, PAL_LIT, PAL_GLASS);
        T(tt_decode_cell(cell, STRIDE, GLYPH_H_MAX_DEFAULT, &d) == g && d == 0, "exact glyph");
    }
    for (int g = 0; g < 10; g++) {         /* noise below h_max still decodes; distance == flips */
        tt_blit_glyph(cell, STRIDE, g, PAL_LIT, PAL_GLASS);
        flip(200, 7u + (unsigned)g);
        T(tt_decode_cell(cell, STRIDE, GLYPH_H_MAX_DEFAULT, &d) == g && d > 0 && d <= 200, "noisy glyph");
    }
    tt_blit_glyph(cell, STRIDE, 8, PAL_LIT, PAL_GLASS);   /* heavy noise -> unreadable, never a wrong digit */
    flip(6000, 99);
    T(tt_decode_cell(cell, STRIDE, GLYPH_H_MAX_DEFAULT, &d) == DEC_UNREADABLE, "heavy noise unreadable");
    tt_fill(cell, STRIDE, GLYPH_W, GLYPH_H, PAL_GLASS);
    T(tt_decode_cell(cell, STRIDE, GLYPH_H_MAX_DEFAULT, &d) == GLYPH_BLANK && d == 0, "glass = blank");
    T(GLYPH_MIN_HAMMING > 4 * GLYPH_H_MAX_DEFAULT - 4, "h_max is a quarter of the min distance");

    int c[3], v;
    int vals[] = {0, 5, 48, 84, 100, 999};
    for (unsigned i = 0; i < sizeof vals / sizeof *vals; i++) {
        tt_cells_for(vals[i], c);
        T(tt_combine_cells(c, &v) == SPD_VALUE && v == vals[i], "cells round trip");
    }
    tt_cells_for(-1, c); T(tt_combine_cells(c, &v) == SPD_DASH, "dash");
    int blank[3] = {GLYPH_BLANK, GLYPH_BLANK, GLYPH_BLANK}; T(tt_combine_cells(blank, &v) == SPD_BLANK, "all blank");
    int lead0[3] = {GLYPH_BLANK, 0, 5}; T(tt_combine_cells(lead0, &v) == SPD_UNREADABLE, "leading zero");
    int hole[3] = {4, GLYPH_BLANK, 8};  T(tt_combine_cells(hole, &v) == SPD_UNREADABLE, "inner blank");
    int unr[3] = {GLYPH_BLANK, 4, DEC_UNREADABLE}; T(tt_combine_cells(unr, &v) == SPD_UNREADABLE, "unreadable cell");

    uint32_t tick[8 * 8];
    for (int k = 0; k < 16; k++) {
        tt_fill(tick, 8, 8, 8, PAL_TICK_BASE + (uint32_t)k);
        T(tt_tick_value(tick, 8) == k, "tick value");
    }
    tick[9] = 0xFF000000u | PAL_GLASS; T(tt_tick_value(tick, 8) == -1, "mixed tick");
    tt_fill(tick, 8, 8, 8, PAL_RED); T(tt_tick_value(tick, 8) == -1, "not a tick");
    for (int y = 0; y < 8; y++) for (int x = 0; x < 8; x++) tick[y * 8 + x] = 0xFF000000u | tt_marker_px(x, y);
    T(tt_marker_ok(tick, 8) == 1, "marker ok"); tick[63] ^= 0x10; T(tt_marker_ok(tick, 8) == 0, "marker broken");

    struct truth t; truth_init(&t);
    float sp[] = {48.4f, 48.2f, 47.6f, 46.0f, 40.0f};
    for (int i = 0; i < 5; i++) {
        struct veh_state s = {.seq = (uint64_t)i + 1, .t_ns = (uint64_t)(i + 1) * 50000000ull, .speed_kmh = sp[i]};
        T(truth_push(&t, &s) == 1, "push");
    }
    struct veh_state dup = {.seq = 3}; T(truth_push(&t, &dup) == 0, "old seq rejected");
    int mn, mx;
    /* window [160 ms, 260 ms]: samples at 200 (46), 250 (40) + the one current at 160 (seq 3 @150 = 48) */
    T(truth_match_speed(&t, 48, 160000000ull, 260000000ull, &mn, &mx) == 1 && mn == 40 && mx == 48, "current-at-t_lo counts");
    T(truth_match_speed(&t, 46, 160000000ull, 260000000ull, 0, 0) == 1, "inside window");
    T(truth_match_speed(&t, 48, 210000000ull, 260000000ull, &mn, &mx) == 0 && mn == 40 && mx == 46, "stale 48 fails");
    T(truth_match_speed(&t, 40, 0, 100000000ull, 0, 0) == 0, "future sample not used");
    struct veh_state skip = {.seq = 8, .t_ns = 400000000ull, .speed_kmh = 0.f};
    truth_push(&t, &skip); T(t.gaps == 1, "seq gap counted");
    T(truth_latest(&t)->seq == 8, "latest");

    /* lamps: decode the lamp row by lit-colour pixel fraction (renderer art = solid 64x64 squares) */
    {
        static uint32_t row[L_LAMP_H * L_LAMP_W];
        for (unsigned bits = 0; bits < 64; bits += 5) {
            tt_fill(row, L_LAMP_W, L_LAMP_W, L_LAMP_H, PAL_GLASS);
            for (int i = 0; i < L_LAMP_N; i++)
                tt_fill(row + L_LAMP[i].x, L_LAMP_W, L_LAMP_S, L_LAMP_S, (bits >> i) & 1 ? L_LAMP[i].rgb : PAL_UNLIT);
            T(tt_lamps_decode(row, L_LAMP_W, 500) == bits, "lamp row round trip");
        }
        tt_fill(row, L_LAMP_W, L_LAMP_W, L_LAMP_H, PAL_GLASS);
        T(tt_lamps_decode(row, L_LAMP_W, 500) == 0, "glass row = no lamp");
        tt_fill(row + L_LAMP[2].x, L_LAMP_W, L_LAMP_S, L_LAMP_S / 4, L_LAMP[2].rgb);   /* a quarter-lit lamp is unlit */
        T(tt_lamp_permille(row + L_LAMP[2].x, L_LAMP_W, L_LAMP_S, L_LAMP_S, L_LAMP[2].rgb, 24) == 250, "quarter = 250 permille");
        T(tt_lamps_decode(row, L_LAMP_W, 500) == 0, "below threshold = unlit");
        tt_fill(row + L_LAMP[2].x, L_LAMP_W, L_LAMP_S, L_LAMP_S, L_LAMP[2].rgb + 0x101010u);  /* small colour drift */
        T(tt_lamps_decode(row, L_LAMP_W, 500) == (1u << 2), "colour within tolerance = lit");
        tt_fill(row + L_LAMP[2].x, L_LAMP_W, L_LAMP_S, L_LAMP_S, L_LAMP[3].rgb);  /* amber where red belongs */
        T(tt_lamps_decode(row, L_LAMP_W, 500) == 0, "wrong colour = unlit");
    }
    /* truth_match_lamps: same window rule as the speed */
    {
        struct truth tl; truth_init(&tl);
        unsigned lb[4] = {0x00, 0x01, 0x05, 0x04};
        for (int i = 0; i < 4; i++) {
            struct veh_state s = {.seq = (uint64_t)i + 1, .t_ns = (uint64_t)(i + 1) * 100000000ull, .lamps = (uint8_t)lb[i]};
            truth_push(&tl, &s);
        }
        T(truth_match_lamps(&tl, 0x04, 0x3F, 350000000ull, 450000000ull), "latest lamps match");
        T(truth_match_lamps(&tl, 0x05, 0x3F, 350000000ull, 450000000ull), "lamps current at t_lo match");
        T(!truth_match_lamps(&tl, 0x01, 0x3F, 350000000ull, 450000000ull), "older lamps outside the window fail");
        T(!truth_match_lamps(&tl, 0x00, 0x3F, 450000000ull, 500000000ull), "missing brake lamp fails");
        T(truth_match_lamps(&tl, 0x00, 0x3B, 450000000ull, 500000000ull), "unchecked bit ignored by the mask");
    }
    printf("test_decode: %d/%d passed\n", runs - fails, runs);
    return fails != 0;
}
