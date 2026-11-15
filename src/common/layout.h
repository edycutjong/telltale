/* Telltale: the fixed cluster layout, shared by the renderer (draws it) and the monitor (reads it back).
 * All x/y are offsets inside the 1280x720 layout window; display coordinates = the window's recorded
 * SCREEN_PROPERTY_POSITION + these offsets (never assumed to start at 0,0).
 * SPDX-License-Identifier: Apache-2.0 */
#ifndef TELLTALE_LAYOUT_H
#define TELLTALE_LAYOUT_H

#define L_WIN_W 1280
#define L_WIN_H 720
/* where the renderer puts its window on the 1280x768 virtual display (spare rows above and below) */
#define L_WIN_X 0
#define L_WIN_Y 24

/* speed: 3 cells, 150x200 each (GLYPH_W x GLYPH_H), right-aligned value, leading zeros blank */
#define L_DIGIT_X0 415
#define L_DIGIT_Y  200
#define L_DIGIT_N  3

/* monitored rects (offsets inside the window) */
#define L_BLOCK_X 415   /* digit + gear block: digits, "km/h", gear strip */
#define L_BLOCK_Y 200
#define L_BLOCK_W 450
#define L_BLOCK_H 336
#define L_LAMP_X 0      /* lamp row */
#define L_LAMP_Y 40
#define L_LAMP_W 1280
#define L_LAMP_H 64
#define L_TICK_X 1264   /* liveness square, 4-bit frame counter in blue */
#define L_TICK_Y 704
#define L_TICK_S 8

/* gear strip P R N D (inside the block) */
#define L_GEAR_X 560
#define L_GEAR_Y 480
#define L_GEAR_W 40
#define L_GEAR_H 56

/* safe-layer window (monitor-owned): covers everything but the bottom 24-px band with the tick */
#define L_SAFE_W 1280
#define L_SAFE_H 696
/* safe-layer-only marker: 8x8, inside the digit block in a margin no renderer element occupies */
#define L_MARK_X 849
#define L_MARK_Y 412
#define L_MARK_S 8
/* fault banner (driver mode) */
#define L_BANNER_Y 600
#define L_BANNER_H 64

#endif
