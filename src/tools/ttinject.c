/* ttinject: fire one fault into the cluster through its test-hook channel /dev/cluster-ctl.
 *   ttinject <freeze|blank|stale|misdraw|crash|heal|lampdrop> [arg] [--run ID] [--at TRACE_S]
 * --at waits (polling /dev/vehicle) until the replayed trace reaches TRACE_S seconds, then fires.
 * Prints the command time (CLOCK_MONOTONIC ns); the cluster stamps the effective fault time itself.
 * SPDX-License-Identifier: Apache-2.0 */
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <devctl.h>
#include "../common/ttproto.h"
#include "../common/ttsec.h"

int main(int argc, char **argv)
{
    static const char *names[] = {"none", "freeze", "blank", "stale", "misdraw", "crash", "heal", "lampdrop"};
    if (argc < 2) { fprintf(stderr, "usage: ttinject <freeze|blank|stale|misdraw|crash|heal|lampdrop> [arg] [--run ID]\n"); return 1; }
    struct cl_fault_cmd c = {0};
    double at = -1;
    for (int i = 1; i < 8; i++) if (!strcmp(argv[1], names[i])) c.cls = i;
    if (!c.cls) { fprintf(stderr, "ttinject: unknown class %s\n", argv[1]); return 1; }
    for (int i = 2; i < argc; i++) {
        if (!strcmp(argv[i], "--run") && i + 1 < argc) c.run_id = strtoull(argv[++i], NULL, 0);
        else if (!strcmp(argv[i], "--at") && i + 1 < argc) at = atof(argv[++i]);
        else c.arg = atoi(argv[i]);
    }
    tt_enter("ttinject", 0, 0);
    int fd = open(PATH_CLCTL, O_RDWR);
    if (fd < 0) { perror(PATH_CLCTL); return 2; }
    if (at >= 0) {
        int vfd = open(PATH_VEHICLE, O_RDONLY);
        struct veh_state s = {0};
        while (vfd >= 0 && (devctl(vfd, DCMD_VEH_GET, &s, sizeof s, NULL) != EOK || s.trace_s < at)) usleep(2000);
        printf("trace_s=%.3f speed_kmh=%.2f\n", s.trace_s, s.speed_kmh);
    }
    uint64_t t = tt_now_ns();
    int rc = devctl(fd, DCMD_CL_FAULT, &c, sizeof c, NULL);
    if (rc != EOK) { fprintf(stderr, "ttinject: DCMD_CL_FAULT: %s\n", strerror(rc)); return 3; }
    printf("t_command_ns=%llu class=%s arg=%d run=%llu\n", (unsigned long long)t, names[c.cls], c.arg, (unsigned long long)c.run_id);
    return 0;
}
