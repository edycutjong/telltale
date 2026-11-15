/* G4 spike: 20 ms periodic timer pulse jitter on the QNX guest.
 * g4 N [prio] [tick_us]  -> CSV: i,t_ns,delta_ns,err_ns (err = delta - 20 ms) to stdout; summary to stderr.
 * timer_create(CLOCK_MONOTONIC, SIGEV_PULSE) + MsgReceivePulse, SCHED_FIFO at prio (default 60). */
#include <errno.h>
#include <pthread.h>
#include <sched.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <sys/neutrino.h>
#include <sys/siginfo.h>

#define PERIOD_NS 20000000LL

static int cmpll(const void *a, const void *b)
{
    long long x = *(const long long *)a, y = *(const long long *)b;
    return (x > y) - (x < y);
}

int main(int argc, char **argv)
{
    int n = argc > 1 ? atoi(argv[1]) : 1000, prio = argc > 2 ? atoi(argv[2]) : 60;
    long tick_us = argc > 3 ? atol(argv[3]) : 0; /* 0 = leave the system tick alone */
    struct _clockperiod cp = {0, 0};
    if (tick_us > 0) {
        struct _clockperiod nc = {(_Uint32t)(tick_us * 1000), 0};
        if (ClockPeriod(CLOCK_REALTIME, &nc, NULL, 0) != 0) perror("ClockPeriod set");
    }
    ClockPeriod(CLOCK_REALTIME, NULL, &cp, 0);
    fprintf(stderr, "clock period %u ns\n", cp.nsec);
    struct sched_param sp = {.sched_priority = prio};
    int rc = pthread_setschedparam(pthread_self(), SCHED_FIFO, &sp);
    fprintf(stderr, "sched FIFO %d: %s\n", prio, rc ? strerror(rc) : "ok");
    int chid = ChannelCreate(_NTO_CHF_PRIVATE), coid = ConnectAttach(0, 0, chid, _NTO_SIDE_CHANNEL, 0);
    struct sigevent ev;
    SIGEV_PULSE_INIT(&ev, coid, prio, _PULSE_CODE_MINAVAIL, 0);
    timer_t tid;
    if (timer_create(CLOCK_MONOTONIC, &ev, &tid) != 0) { perror("timer_create"); return 2; }
    struct itimerspec it = {{0, PERIOD_NS}, {0, PERIOD_NS}};
    timer_settime(tid, 0, &it, NULL);
    long long *err = malloc(sizeof(long long) * (size_t)n), prev = 0;
    struct _pulse p;
    printf("i,t_ns,delta_ns,err_ns\n");
    for (int i = -1; i < n; i++) {
        MsgReceivePulse(chid, &p, sizeof p, NULL);
        struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t);
        long long now = (long long)t.tv_sec * 1000000000LL + t.tv_nsec;
        if (i >= 0) {
            long long d = now - prev;
            err[i] = d - PERIOD_NS;
            printf("%d,%lld,%lld,%lld\n", i, now, d, err[i]);
        }
        prev = now;
    }
    for (int i = 0; i < n; i++) err[i] = llabs(err[i]);
    qsort(err, (size_t)n, sizeof *err, cmpll);
    int k95 = (int)((95LL * n + 99) / 100) - 1, k50 = (int)((50LL * n + 99) / 100) - 1; /* nearest rank */
    fprintf(stderr, "SUMMARY n=%d period_ms=20 prio=%d tick_ns=%u abs_err_p50_us=%.1f p95_us=%.1f max_us=%.1f\n",
            n, prio, cp.nsec, err[k50] / 1e3, err[k95] / 1e3, err[n - 1] / 1e3);
    return 0;
}
