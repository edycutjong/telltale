/* Telltale: the one rule for "what number should the cluster show".
 * The renderer draws disp(speed) and the monitor compares the decoded
 * digits against disp(truth); both sides must use this function.
 * SPDX-License-Identifier: Apache-2.0 */
#ifndef TELLTALE_DISP_H
#define TELLTALE_DISP_H

#define DISP_MAX_KMH 999

/* Round half up to an integer km/h, clamped to [0, DISP_MAX_KMH].
 * NaN maps to -1 (no valid speed: the renderer shows a dash). */
int disp_kmh(float speed_kmh);

/* m/s (comma2k19 CAN speed) to km/h. */
float mps_to_kmh(float speed_mps);

#endif
