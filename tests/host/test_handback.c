/* Host unit test for the monitor's SAFE-HOLD hand-back rule (src/common/handback.h). Pure C, no QNX APIs.
 * Regression (final code review, finding 2): the hand-back needed only "window reads true and not stalled". A check that
 * starts 266 ms or more after the previous one restarts the stall clock (t_tick_change) without a tick move, so a renderer
 * frozen at a value that is still true was handed back. The stamps below follow telltale.c: t_tick_real moves only on a
 * genuine tick change, t_detect is the detecting check's t_cap_end.
 * SPDX-License-Identifier: Apache-2.0 */
#include <stdio.h>
#include "../../src/common/handback.h"

static int failures, checks;
#define EXPECT_EQ(got, want) do { checks++; if ((got) != (want)) { failures++; \
    fprintf(stderr, "%s:%d: got %d want %d\n", __FILE__, __LINE__, (int)(got), (int)(want)); } } while (0)

#define MS 1000000ull
int main(void)
{
    const uint64_t t_detect = 1000 * MS;   /* FREEZE detected (TICK_STALL) at 1000 ms */
    uint64_t t_tick_real = 840 * MS;       /* the renderer's last genuine tick change, before it froze */

    /* the scenario of finding 2: a late SAFE-HOLD check (>= 266 ms after the previous one) restarts the stall clock, so it is
     * not stalled, and the frozen frame still equals the truth (constant speed) -- no genuine move since detection: hold */
    EXPECT_EQ(tt_handback_ok(1, 0, t_tick_real, t_detect), 0);
    /* the stamp of the detecting check itself is not "after detection" */
    EXPECT_EQ(tt_handback_ok(1, 0, t_detect, t_detect), 0);

    /* the renderer draws again (a respawn, a heal, a starvation that ended): its tick moves after detection -> hand back */
    t_tick_real = 1320 * MS;
    EXPECT_EQ(tt_handback_ok(1, 0, t_tick_real, t_detect), 1);

    /* never while its own window is not true, or while a stall is seen, whatever the tick did */
    EXPECT_EQ(tt_handback_ok(0, 0, t_tick_real, t_detect), 0);
    EXPECT_EQ(tt_handback_ok(1, 1, t_tick_real, t_detect), 0);
    EXPECT_EQ(tt_handback_ok(0, 1, t_tick_real, t_detect), 0);

    /* no tick seen at all yet (t_tick_real 0) never hands back */
    EXPECT_EQ(tt_handback_ok(1, 0, 0, t_detect), 0);

    printf("test_handback: %d checks, %d failures\n", checks, failures);
    return failures ? 1 : 0;
}
