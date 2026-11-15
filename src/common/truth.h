/* Telltale: the monitor's truth window -- a ring of the last VEH_RING vehicle samples.
 * A region passes if it shows a value that was true at some instant in [t_lo, t_hi]: the samples
 * accepted inside the interval, plus the one already current at t_lo (it was the truth until replaced).
 * SPDX-License-Identifier: Apache-2.0 */
#ifndef TELLTALE_TRUTH_H
#define TELLTALE_TRUTH_H
#include <stdint.h>
#include "vehicle.h"
#define TRUTH_N 64
struct truth {
    struct veh_state s[TRUTH_N];
    unsigned head, count;
    uint64_t last_seq, gaps;
};
void truth_init(struct truth *t);
/* push samples in seq order; returns 1 if accepted (seq > last_seq); counts seq gaps */
int truth_push(struct truth *t, const struct veh_state *s);
const struct veh_state *truth_latest(const struct truth *t);
/* 1 if disp_kmh(speed) == shown for some sample true during [t_lo, t_hi]; mn, mx = disp range seen */
int truth_match_speed(const struct truth *t, int shown, uint64_t t_lo, uint64_t t_hi, int *mn, int *mx);
/* 1 if (lamps & mask) == shown for some sample true during [t_lo, t_hi] (same window rule as the speed) */
int truth_match_lamps(const struct truth *t, unsigned shown, unsigned mask, uint64_t t_lo, uint64_t t_hi);
#endif
