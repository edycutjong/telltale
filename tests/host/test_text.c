/* host tests: banner/card text rendering (src/common/text.c)
 * SPDX-License-Identifier: Apache-2.0 */
#include <stdio.h>
#include <string.h>
#include "../../src/common/text.h"

static int fails, n;
#define CHECK(c) do { n++; if (!(c)) { fails++; printf("FAIL %s:%d %s\n", __FILE__, __LINE__, #c); } } while (0)

#define W 400
#define H 48
static uint32_t buf[H * W];
static void fill(uint32_t v) { for (int i = 0; i < H * W; i++) buf[i] = v; }
static int count(uint32_t v) { int c = 0; for (int i = 0; i < H * W; i++) c += buf[i] == v; return c; }

int main(void)
{
    const struct tt_font *b = &tt_font_banner, *c = &tt_font_card;
    CHECK(b->h > c->h && c->h >= 20);                       /* no text < 20 px on the cluster */
    CHECK(tt_text_width(c, "") == 0);
    CHECK(tt_text_width(c, " ") > 0);
    CHECK(tt_text_width(c, "AB") == tt_text_width(c, "A") + tt_text_width(c, "B"));
    CHECK(tt_text_width(c, TT_MIDDOT) > 0);                 /* U+00B7 has a glyph */
    CHECK(tt_text_width(c, "\xC2") == 0);                   /* a lone lead byte draws nothing */
    CHECK(tt_text_width(c, "\x01" "A") == tt_text_width(c, "A"));
    CHECK(tt_text_width(b, "DISPLAY FAULT " TT_MIDDOT " SHOWING TRUE SPEED") < 1280);
    CHECK(tt_text_width(c, "service: cat /dev/telltale") < 400);

    const uint32_t BG = 0xFF1A1916u, FG = 0xFFF4F1E8u;
    fill(BG);
    int end = tt_text_draw(buf, W, W, H, 4, 4, c, "TT-FRZ-0007", FG, BG);
    CHECK(end == 4 + tt_text_width(c, "TT-FRZ-0007"));
    CHECK(count(FG) > 50);                                 /* fully covered pixels are exactly fg */
    int inter = 0, out_of_range = 0, transparent = 0;      /* anti-aliased edge pixels lie between bg and fg */
    for (int i = 0; i < H * W; i++) {
        if ((buf[i] >> 24) != 0xFF) transparent++;
        if (buf[i] == BG || buf[i] == FG) continue;
        inter++;
        int g = (int)(buf[i] >> 8) & 0xFF;
        if (g < 0x19 || g > 0xF1) out_of_range++;
    }
    CHECK(inter > 0);
    CHECK(out_of_range == 0);
    CHECK(transparent == 0);                               /* opaque */

    /* nothing drawn outside the ink box: rows below the line stay bg */
    int below = 0;
    for (int y = 4 + c->h; y < H; y++) for (int x = 0; x < W; x++) below += buf[y * W + x] != BG;
    CHECK(below == 0);

    /* clipping: drawing past every edge must not write out of bounds or crash */
    fill(BG);
    tt_text_draw(buf, W, W, H, -30, -10, b, "CLIP " TT_MIDDOT " TEST", FG, BG);
    tt_text_draw(buf, W, W, H, W - 20, H - 10, b, "WWWW", FG, BG);
    CHECK(count(FG) > 0);

    /* same input, same pixels (the frame is reproducible) */
    static uint32_t a2[H * W];
    fill(BG); tt_text_draw(buf, W, W, H, 2, 2, c, "shown 48 " TT_MIDDOT " truth 0 km/h", FG, BG);
    memcpy(a2, buf, sizeof a2);
    fill(BG); tt_text_draw(buf, W, W, H, 2, 2, c, "shown 48 " TT_MIDDOT " truth 0 km/h", FG, BG);
    CHECK(memcmp(a2, buf, sizeof a2) == 0);

    printf("test_text: %d/%d passed\n", n - fails, n);
    return fails != 0;
}
