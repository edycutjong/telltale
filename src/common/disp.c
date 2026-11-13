/* SPDX-License-Identifier: Apache-2.0 */
#include <math.h>
#include "disp.h"

int disp_kmh(float speed_kmh)
{
    if (isnan(speed_kmh))
        return -1;
    if (speed_kmh <= 0.0f)
        return 0;
    double r = floor((double)speed_kmh + 0.5);
    if (r > DISP_MAX_KMH)
        return DISP_MAX_KMH;
    return (int)r;
}

float mps_to_kmh(float speed_mps)
{
    return speed_mps * 3.6f;
}
