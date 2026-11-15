/* hog: the STARVE fault -- an external busy loop at a fixed SCHED_FIFO priority (default 40) for <ms>,
 * optionally pinned to one CPU. No bug is planted in the renderer: it is simply out-prioritised.
 *   hog <ms> [prio] [cpu]
 * SPDX-License-Identifier: Apache-2.0 */
#include <stdio.h>
#include <stdlib.h>
#include <sys/neutrino.h>
#include "../common/ttsec.h"

int main(int argc, char **argv)
{
    if (argc < 2) { fprintf(stderr, "usage: hog <ms> [prio] [cpu]\n"); return 1; }
    uint64_t ms = strtoull(argv[1], NULL, 0);
    int prio = argc > 2 ? atoi(argv[2]) : 40;
    if (argc > 3) {
        unsigned mask = 1u << atoi(argv[3]);
        if (ThreadCtl(_NTO_TCTL_RUNMASK, (void *)(uintptr_t)mask) == -1) perror("hog: runmask");
    }
    tt_enter("hog", SCHED_FIFO, prio);
    uint64_t t0 = tt_now_ns(), end = t0 + ms * 1000000ull;
    printf("t_hog_start_ns=%llu prio=%d ms=%llu\n", (unsigned long long)t0, prio, (unsigned long long)ms); fflush(stdout);
    volatile unsigned long spin = 0;
    while (tt_now_ns() < end) spin++;
    printf("t_hog_end_ns=%llu\n", (unsigned long long)tt_now_ns());
    return 0;
}
