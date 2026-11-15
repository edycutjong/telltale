/* hog: the STARVE fault -- an external busy loop at a fixed SCHED_FIFO priority (default 40) for <ms>,
 * optionally pinned to one CPU. No bug is planted in the renderer: it is simply out-prioritised.
 *   hog <ms> [prio] [cpu] [--at TRACE_S]
 * --at waits (polling /dev/vehicle, before raising priority) until the replayed trace reaches TRACE_S seconds.
 * SPDX-License-Identifier: Apache-2.0 */
#include <stdio.h>
#include <stdlib.h>
#include <errno.h>
#include <string.h>
#include <fcntl.h>
#include <unistd.h>
#include <devctl.h>
#include <pthread.h>
#include <sched.h>
#include <sys/neutrino.h>
#include "../common/ttproto.h"
#include "../common/ttsec.h"

int main(int argc, char **argv)
{
    if (argc < 2) { fprintf(stderr, "usage: hog <ms> [prio] [cpu] [--at TRACE_S]\n"); return 1; }
    double at = -1;
    int pos[3], np = 0;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--at") && i + 1 < argc) at = atof(argv[++i]);
        else if (np < 3) pos[np++] = i;
    }
    if (!np) { fprintf(stderr, "usage: hog <ms> [prio] [cpu] [--at TRACE_S]\n"); return 1; }
    uint64_t ms = strtoull(argv[pos[0]], NULL, 0);
    int prio = np > 1 ? atoi(argv[pos[1]]) : 40;
    int cpu = np > 2 ? atoi(argv[pos[2]]) : -1;
    if (cpu >= 32) { fprintf(stderr, "hog: cpu %d out of range (0..31)\n", cpu); return 2; }
    if (cpu >= 0) {
        unsigned mask = 1u << cpu;
        if (ThreadCtl(_NTO_TCTL_RUNMASK, (void *)(uintptr_t)mask) == -1) perror("hog: runmask");
    }
    if (at >= 0) {
        int vfd = open(PATH_VEHICLE, O_RDONLY);
        struct veh_state s = {0};
        while (vfd >= 0 && (devctl(vfd, DCMD_VEH_GET, &s, sizeof s, NULL) != EOK || s.trace_s < at)) usleep(2000);
        printf("trace_s=%.3f speed_kmh=%.2f\n", s.trace_s, s.speed_kmh);
        if (vfd >= 0) close(vfd);
    }
    /* type first, priority second: under the policy hog_t holds priority:1-40 only, so a higher request fails */
    tt_enter("hog", 0, 0);
    struct sched_param sp = {.sched_priority = prio};
    int rc = pthread_setschedparam(pthread_self(), SCHED_FIFO, &sp);
    if (rc) { fprintf(stderr, "hog: setschedparam(%d) as %s: %s\n", prio, tt_type_name(), strerror(rc)); return 2; }
    uint64_t t0 = tt_now_ns(), end = t0 + ms * 1000000ull;
    printf("t_hog_start_ns=%llu prio=%d ms=%llu cpu=%d\n", (unsigned long long)t0, prio, (unsigned long long)ms, cpu); fflush(stdout);
    volatile unsigned long spin = 0;
    while (tt_now_ns() < end) spin++;
    printf("t_hog_end_ns=%llu\n", (unsigned long long)tt_now_ns());
    return 0;
}
