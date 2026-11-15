/* Telltale: the monitor's restart cap -- a ring of the last RCAP_N renderer kill times.
 * The cap is reached when `max` kills fall inside the last `window_ns`. The write index rotates on its own,
 * so the ring keeps counting correctly after more than RCAP_N kills in the monitor's lifetime.
 * Pure C, no QNX APIs (host-tested in tests/host/test_rcap.c).
 * SPDX-License-Identifier: Apache-2.0 */
#ifndef TELLTALE_RCAP_H
#define TELLTALE_RCAP_H
#include <stdint.h>
#define RCAP_N 8
struct rcap { uint64_t t[RCAP_N]; unsigned n, next; };

static inline void rcap_reset(struct rcap *r) { r->n = 0; r->next = 0; }

static inline void rcap_note(struct rcap *r, uint64_t now)
{
    r->t[r->next] = now;
    r->next = (r->next + 1) % RCAP_N;
    if (r->n < RCAP_N) r->n++;
}

static inline int rcap_recent(const struct rcap *r, uint64_t now, uint64_t window_ns)
{
    int k = 0;
    for (unsigned i = 0; i < r->n; i++) if (now - r->t[i] < window_ns) k++;
    return k;
}

static inline int rcap_reached(const struct rcap *r, uint64_t now, uint64_t window_ns, int max)
{
    return rcap_recent(r, now, window_ns) >= max;
}
#endif
