/* Telltale: anti-aliased text for the monitor's fault banner and incident card (never decoded, display only).
 * Atlases come from tools/gen_text.py (src/common/textfont.h). Strings are ASCII; the UTF-8 middle dot
 * "\xC2\xB7" (U+00B7) is supported; any other byte draws nothing and advances nothing.
 * SPDX-License-Identifier: Apache-2.0 */
#ifndef TELLTALE_TEXT_H
#define TELLTALE_TEXT_H
#include <stdint.h>

#define TT_FONT_N 96   /* ASCII 32..126 + U+00B7 at index 95 */
#define TT_MIDDOT "\xC2\xB7"

struct tt_font {
    int h, asc;                       /* line height and ascent (px) */
    const unsigned char *adv;         /* advance per glyph */
    const signed char *xo;            /* ink x offset from the pen */
    const unsigned char *w;           /* ink width */
    const unsigned *off;              /* offset of the glyph's coverage map in data */
    const char *data;                 /* 4-bit coverage, one hex char per pixel, w x h row-major */
};
extern const struct tt_font tt_font_banner, tt_font_card;

/* width in px of s set in f */
int tt_text_width(const struct tt_font *f, const char *s);
/* draw s with its top-left at (x, y) inside a w x h ARGB buffer (stride in px), blending fg over bg by coverage;
 * clipped to the buffer. Returns the pen x after the last glyph. */
int tt_text_draw(uint32_t *px, int stride, int w, int h, int x, int y, const struct tt_font *f, const char *s,
                 uint32_t fg, uint32_t bg);

#endif
