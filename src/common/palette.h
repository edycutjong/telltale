/* Telltale: cluster palette as 0x00RRGGBB (specs: --glass, --lit, ISO lamp colours).
 * SPDX-License-Identifier: Apache-2.0 */
#ifndef TELLTALE_PALETTE_H
#define TELLTALE_PALETTE_H
#define PAL_GLASS 0x0E0D0Bu
#define PAL_LIT   0xF4F1E8u
#define PAL_UNLIT 0x262420u
#define PAL_MUTED 0x8A857Au
#define PAL_RED   0xE5322Du
#define PAL_AMBER 0xF2A900u
#define PAL_GREEN 0x2FB34Au
#define PAL_BLUE  0x2F6FE0u
/* tick square: R,G fixed, B = 0x08 + (frame & 0x0F); invisible on the glass, exact to the read-back */
#define PAL_TICK_BASE 0x0E0D08u
#endif
