/* SPDX-License-Identifier: Apache-2.0 */
#include <string.h>
#include "truth.h"
#include "disp.h"

void truth_init(struct truth *t) { memset(t, 0, sizeof *t); }

int truth_push(struct truth *t, const struct veh_state *s)
{
    if (t->count && s->seq <= t->last_seq) return 0;
    if (t->count && s->seq != t->last_seq + 1) t->gaps++;
    t->s[t->head] = *s;
    t->head = (t->head + 1) % TRUTH_N;
    if (t->count < TRUTH_N) t->count++;
    t->last_seq = s->seq;
    return 1;
}

const struct veh_state *truth_latest(const struct truth *t)
{
    return t->count ? &t->s[(t->head + TRUTH_N - 1) % TRUTH_N] : 0;
}

int truth_match_speed(const struct truth *t, int shown, uint64_t t_lo, uint64_t t_hi, int *mn, int *mx)
{
    int hit = 0, lo = 1 << 30, hi = -(1 << 30), have_before = 0;
    for (unsigned k = 0; k < t->count; k++) {                    /* newest first */
        const struct veh_state *s = &t->s[(t->head + TRUTH_N - 1 - k) % TRUTH_N];
        if (s->t_ns > t_hi) continue;
        if (s->t_ns < t_lo) { if (have_before) break; have_before = 1; } /* the one current at t_lo */
        int d = disp_kmh(s->speed_kmh);
        if (d < lo) lo = d;
        if (d > hi) hi = d;
        if (d == shown) hit = 1;
        if (have_before) break;
    }
    if (mn) *mn = lo == (1 << 30) ? -1 : lo;
    if (mx) *mx = hi == -(1 << 30) ? -1 : hi;
    return hit;
}
