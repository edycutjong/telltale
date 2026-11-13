/* Host unit test for disp_kmh() / mps_to_kmh(). Pure C, no QNX APIs.
 * SPDX-License-Identifier: Apache-2.0 */
#include <math.h>
#include <stdio.h>
#include "../../src/common/disp.h"

static int failures, checks;

#define EXPECT_EQ(got, want) do { \
    checks++; \
    if ((got) != (want)) { \
        failures++; \
        fprintf(stderr, "%s:%d: got %d want %d\n", __FILE__, __LINE__, (int)(got), (int)(want)); \
    } } while (0)

int main(void)
{
    EXPECT_EQ(disp_kmh(48.0f), 48);
    EXPECT_EQ(disp_kmh(47.5f), 48);   /* half rounds up */
    EXPECT_EQ(disp_kmh(47.49f), 47);
    EXPECT_EQ(disp_kmh(48.49f), 48);
    EXPECT_EQ(disp_kmh(0.4f), 0);
    EXPECT_EQ(disp_kmh(0.5f), 1);
    EXPECT_EQ(disp_kmh(0.0f), 0);
    EXPECT_EQ(disp_kmh(-3.0f), 0);    /* reverse creep never shows negative */
    EXPECT_EQ(disp_kmh(1200.0f), DISP_MAX_KMH);
    EXPECT_EQ(disp_kmh(NAN), -1);
    EXPECT_EQ(disp_kmh(mps_to_kmh(13.3333f)), 48);  /* 13.33 m/s = 48.0 km/h */
    EXPECT_EQ(disp_kmh(mps_to_kmh(0.0f)), 0);

    printf("test_disp: %d/%d checks passed\n", checks - failures, checks);
    return failures ? 1 : 0;
}
