/* vehfeed: replays a real drive trace (CSV t_s,speed_mps) into /dev/vehicle in real time (SCHED_FIFO 45).
 *   vehfeed <trace.csv> [--hold S] [--loop] [--gear D] [--lamps 0xNN]
 * --hold S holds the first sample for S seconds before playback (a steady speed before the drive starts).
 * After the last row it keeps publishing that row every 50 ms (the parked car), until killed.
 * SPDX-License-Identifier: Apache-2.0 */
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <devctl.h>
#include "../common/ttproto.h"
#include "../common/ttsec.h"
#include "../common/disp.h"

int main(int argc, char **argv)
{
    if (argc < 2) { fprintf(stderr, "usage: vehfeed <trace.csv> [--hold S] [--loop] [--gear PRND] [--lamps 0xNN]\n"); return 1; }
    double hold = 0; int loop = 0; uint8_t gear = GEAR_D, lamps = 0;
    for (int i = 2; i < argc; i++) {
        if (!strcmp(argv[i], "--hold") && i + 1 < argc) hold = atof(argv[++i]);
        else if (!strcmp(argv[i], "--loop")) loop = 1;
        else if (!strcmp(argv[i], "--gear") && i + 1 < argc) { const char *g = strchr("PRND", argv[++i][0]); gear = g ? (uint8_t)(g - "PRND") : GEAR_D; }
        else if (!strcmp(argv[i], "--lamps") && i + 1 < argc) lamps = (uint8_t)strtoul(argv[++i], NULL, 0);
    }
    FILE *f = fopen(argv[1], "r");
    if (!f) { perror(argv[1]); return 1; }
    size_t cap = 1 << 16, n = 0;
    double *t = malloc(cap * sizeof *t), *v = malloc(cap * sizeof *v);
    char line[128];
    while (fgets(line, sizeof line, f)) {
        double a, b;
        if (sscanf(line, "%lf,%lf", &a, &b) != 2) continue;   /* header */
        if (n == cap) { cap *= 2; t = realloc(t, cap * sizeof *t); v = realloc(v, cap * sizeof *v); }
        t[n] = a; v[n] = b; n++;
    }
    fclose(f);
    if (!n) { fprintf(stderr, "vehfeed: no rows in %s\n", argv[1]); return 1; }
    tt_enter("vehfeed", SCHED_FIFO, 45);
    int fd = -1;
    for (int tries = 0; tries < 100 && fd < 0; tries++) { fd = open(PATH_VEHICLE, O_RDWR); if (fd < 0) usleep(50000); }
    if (fd < 0) { perror(PATH_VEHICLE); return 1; }
    printf("vehfeed: %zu rows, %.1f s, hold %.1f s, loop %d\n", n, t[n - 1] - t[0], hold, loop); fflush(stdout);
    do {
        struct timespec t0; clock_gettime(CLOCK_MONOTONIC, &t0);
        uint64_t base = (uint64_t)t0.tv_sec * 1000000000ull + (uint64_t)t0.tv_nsec;
        if (hold > 0) {   /* repeat the first sample every 50 ms */
            for (uint64_t k = 0; k * 50000000ull < (uint64_t)(hold * 1e9); k++) {
                uint64_t at = base + k * 50000000ull;
                struct timespec ts = {(time_t)(at / 1000000000ull), (long)(at % 1000000000ull)};
                clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &ts, NULL);
                struct veh_state s = {.trace_s = (float)t[0], .speed_kmh = mps_to_kmh((float)v[0]), .gear = gear, .lamps = lamps};
                int rc = devctl(fd, DCMD_VEH_SET, &s, sizeof s, NULL);
                if (rc != EOK) { fprintf(stderr, "vehfeed: DCMD_VEH_SET: %s\n", strerror(rc)); return 3; }
            }
            base += (uint64_t)(hold * 1e9);
        }
        for (size_t i = 0; i < n; i++) {
            uint64_t at = base + (uint64_t)((t[i] - t[0]) * 1e9);
            struct timespec ts = {(time_t)(at / 1000000000ull), (long)(at % 1000000000ull)};
            clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &ts, NULL);
            struct veh_state s = {.trace_s = (float)t[i], .speed_kmh = mps_to_kmh((float)v[i]), .gear = gear, .lamps = lamps};
            int rc = devctl(fd, DCMD_VEH_SET, &s, sizeof s, NULL);
            if (rc != EOK) { fprintf(stderr, "vehfeed: DCMD_VEH_SET: %s\n", strerror(rc)); return 3; }
        }
        hold = 0;
    } while (loop);
    printf("vehfeed: trace done; holding the last sample (parked) every 50 ms\n"); fflush(stdout);
    for (;;) {   /* the car is still there: keep the truth fresh so the end of the file is not a data loss */
        usleep(50000);
        struct veh_state s = {.trace_s = (float)t[n - 1], .speed_kmh = mps_to_kmh((float)v[n - 1]), .gear = gear, .lamps = lamps};
        if (devctl(fd, DCMD_VEH_SET, &s, sizeof s, NULL) != EOK) return 3;
    }
}
