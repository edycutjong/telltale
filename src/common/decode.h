/* Telltale: pixel decoding shared by the monitor (and host tests). Pixels are 32-bit 0xAARRGGBB
 * words (Screen RGBA8888 as read back), addressed with a stride in pixels.
 * SPDX-License-Identifier: Apache-2.0 */
#ifndef TELLTALE_DECODE_H
#define TELLTALE_DECODE_H
#include <stdint.h>

#define DEC_UNREADABLE (-1)

/* Decode one 150x200 digit cell at base. Returns the glyph index (0-9, GLYPH_DASH, GLYPH_BLANK)
 * whose Hamming distance is smallest and <= h_max, else DEC_UNREADABLE. *dist = best distance. */
int tt_decode_cell(const uint32_t *base, int stride_px, int h_max, int *dist);

enum speed_kind { SPD_VALUE = 0, SPD_DASH = 1, SPD_BLANK = 2, SPD_UNREADABLE = 3 };
/* Combine 3 cell results (left to right) into a speed: leading blanks, then digits, no leading zero.
 * blank,dash,dash = SPD_DASH (no valid speed); all blank = SPD_BLANK. */
enum speed_kind tt_combine_cells(const int cell[3], int *value);

/* the inverse, used by the renderer: which glyph goes in each cell for disp value v (-1 = dash) */
void tt_cells_for(int v, int cell[3]);

/* 8x8 tick square: 0..15 if every pixel is PAL_TICK_BASE + v, else -1 */
int tt_tick_value(const uint32_t *base, int stride_px);

/* safe-layer marker (8x8 checker of red / lit); 1 if base matches exactly */
uint32_t tt_marker_px(int x, int y);
int tt_marker_ok(const uint32_t *base, int stride_px);

/* lamps: permille of the w x h pixels within +-tol per channel of rgb */
int tt_lamp_permille(const uint32_t *base, int stride_px, int w, int h, uint32_t rgb, int tol);
/* decode the lamp row (base = pixel (0, L_LAMP_Y) of the layout): bit i set iff lamp i's permille >= thr */
unsigned tt_lamps_decode(const uint32_t *row, int stride_px, int thr_permille);

/* render helpers (renderer and safe layer share them, so both draw identical bits) */
void tt_blit_glyph(uint32_t *base, int stride_px, int glyph, uint32_t fg, uint32_t bg);
void tt_fill(uint32_t *base, int stride_px, int w, int h, uint32_t rgb);
#endif
