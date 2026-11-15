/* Telltale: text rendering for the banner and the incident card (see text.h).
 * SPDX-License-Identifier: Apache-2.0 */
#include "text.h"
#include "textfont.h"

/* next glyph index from s (advances *s), -1 for a byte with no glyph */
static int next_glyph(const char **s)
{
    unsigned char c = (unsigned char)**s;
    (*s)++;
    if (c >= 32 && c <= 126) return c - 32;
    if (c == 0xC2 && (unsigned char)**s == 0xB7) { (*s)++; return 95; }
    return -1;
}

int tt_text_width(const struct tt_font *f, const char *s)
{
    int w = 0;
    while (*s) { int g = next_glyph(&s); if (g >= 0) w += f->adv[g]; }
    return w;
}

static uint32_t blend(uint32_t fg, uint32_t bg, int a)   /* a in 0..15 */
{
    uint32_t r = 0;
    for (int sh = 0; sh <= 16; sh += 8) {
        int cf = (int)(fg >> sh) & 0xFF, cb = (int)(bg >> sh) & 0xFF;
        r |= (uint32_t)((cb * (15 - a) + cf * a + 7) / 15) << sh;
    }
    return 0xFF000000u | r;
}

int tt_text_draw(uint32_t *px, int stride, int w, int h, int x, int y, const struct tt_font *f, const char *s,
                 uint32_t fg, uint32_t bg)
{
    while (*s) {
        int g = next_glyph(&s);
        if (g < 0) continue;
        const char *cov = f->data + f->off[g];
        int gx = x + f->xo[g];
        for (int r = 0; r < f->h; r++) {
            int yy = y + r;
            if (yy < 0 || yy >= h) continue;
            for (int c = 0; c < f->w[g]; c++) {
                int xx = gx + c;
                char ch = cov[r * f->w[g] + c];
                int a = ch <= '9' ? ch - '0' : ch - 'a' + 10;
                if (a && xx >= 0 && xx < w) px[(long)yy * stride + xx] = blend(fg, bg, a);
            }
        }
        x += f->adv[g];
    }
    return x;
}
