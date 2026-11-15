/* Host unit test for the monitor's restart cap (src/common/rcap.h). Pure C, no QNX APIs.
 * Regression: the old in-line ring wrote kills[nkills++ % 8] with nkills clamped to 8, so after the 8th kill
 * every later kill landed in slot 0 and a crash loop could never reach the cap again.
 * SPDX-License-Identifier: Apache-2.0 */
#include <stdio.h>
#include "../../src/common/rcap.h"

static int failures, checks;
#define EXPECT_EQ(got, want) do { checks++; if ((got) != (want)) { failures++; \
    fprintf(stderr, "%s:%d: got %d want %d\n", __FILE__, __LINE__, (int)(got), (int)(want)); } } while (0)

#define S 1000000000ull
int main(void)
{
    const uint64_t W = 60 * S;      /* restart_window_s = 60 */
    const int MAX = 3;              /* max_restarts = 3 */
    struct rcap r; rcap_reset(&r);

    /* three kills inside the window reach the cap; two do not */
    rcap_note(&r, 1 * S); rcap_note(&r, 2 * S);
    EXPECT_EQ(rcap_reached(&r, 3 * S, W, MAX), 0);
    rcap_note(&r, 3 * S);
    EXPECT_EQ(rcap_reached(&r, 3 * S, W, MAX), 1);
    /* the window slides: 70 s later none of them counts */
    EXPECT_EQ(rcap_recent(&r, 70 * S, W), 0);

    /* lifetime > RCAP_N kills, spread out (one per 61 s), then a burst: the cap must still fire */
    rcap_reset(&r);
    uint64_t t = 0;
    for (int i = 0; i < 20; i++) { t += 61 * S; rcap_note(&r, t); EXPECT_EQ(rcap_reached(&r, t, W, MAX), 0); }
    EXPECT_EQ((int)r.n, RCAP_N);
    rcap_note(&r, t + 1 * S);
    rcap_note(&r, t + 2 * S);       /* with t itself: 3 kills inside 60 s */
    EXPECT_EQ(rcap_recent(&r, t + 2 * S, W), 3);
    EXPECT_EQ(rcap_reached(&r, t + 2 * S, W, MAX), 1);

    /* rearm (TT_REARM) clears the history */
    rcap_reset(&r);
    EXPECT_EQ(rcap_recent(&r, t + 3 * S, W), 0);

    /* the ring holds exactly the last RCAP_N times */
    rcap_reset(&r);
    for (int i = 1; i <= 11; i++) rcap_note(&r, (uint64_t)i * S);
    uint64_t lo = ~0ull; for (unsigned i = 0; i < r.n; i++) if (r.t[i] < lo) lo = r.t[i];
    EXPECT_EQ((int)(lo / S), 11 - RCAP_N + 1);

    printf("test_rcap: %d checks, %d failures\n", checks, failures);
    return failures ? 1 : 0;
}
