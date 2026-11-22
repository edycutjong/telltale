/* Telltale: the monitor's SAFE-HOLD hand-back rule. The display goes back to the renderer only when
 *   - its own window (screen_read_window) reads true,
 *   - no tick stall is seen, and
 *   - its tick has genuinely moved since the incident was detected (t_tick_real > t_detect).
 * The third condition matters after a late check: a check that starts 266 ms or more after the previous one restarts the
 * stall clock without a tick move (the 4-bit tick may have wrapped), so "not stalled" alone would hand back a renderer frozen
 * at a value that is still true. t_tick_real is stamped only by a genuine tick change, never by that lateness reset.
 * Pure C, no QNX APIs (host-tested in tests/host/test_handback.c).
 * SPDX-License-Identifier: Apache-2.0 */
#ifndef TELLTALE_HANDBACK_H
#define TELLTALE_HANDBACK_H
#include <stdint.h>

static inline int tt_handback_ok(int window_true, int stalled, uint64_t t_tick_real, uint64_t t_detect)
{
    return window_true && !stalled && t_tick_real > t_detect;
}
#endif
