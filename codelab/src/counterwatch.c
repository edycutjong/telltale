/* counterwatch.c - wait for /dev/counter to change WITHOUT polling: arm an ionotify() pulse and block in
 * MsgReceivePulse(). Prints each new value; exits after <count> changes (default 3).
 *   counterwatch [count]
 */
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <sys/neutrino.h>
#include <sys/siginfo.h>
#include <sys/iomsg.h>
#include "counter.h"

#define PULSE_CODE (_PULSE_CODE_MINAVAIL + 1)

static void print_value(int fd)
{
    char buf[32];
    lseek(fd, 0, SEEK_SET);                      /* a fresh read of the same open file */
    ssize_t n = read(fd, buf, sizeof buf - 1);
    if (n > 0) { buf[n] = 0; printf("counterwatch: value is now %s", buf); fflush(stdout); }
}

int main(int argc, char **argv)
{
    int want = argc > 1 ? atoi(argv[1]) : 3;
    int fd = open(COUNTER_DEVPATH, O_RDWR);
    if (fd == -1) { perror("open " COUNTER_DEVPATH); return 1; }

    int chid = ChannelCreate(0);
    int coid = ConnectAttach(0, 0, chid, _NTO_SIDE_CHANNEL, 0);
    struct sigevent ev;
    SIGEV_PULSE_INIT(&ev, coid, SIGEV_PULSE_PRIO_INHERIT, PULSE_CODE, 0);
    /* QNX 8.0: an event handed to ionotify() must first be registered against that fd, otherwise the
     * resource manager's MsgDeliverEvent() has nothing it is allowed to deliver and no pulse ever arrives. */
    if (MsgRegisterEvent(&ev, fd) == -1) { perror("MsgRegisterEvent"); return 1; }

    printf("counterwatch: armed, waiting for %d change(s)\n", want);
    fflush(stdout);
    for (int seen = 0; seen < want;) {
        /* POLLARM: report conditions that are true now; arm the pulse only if input is NOT ready yet. */
        int now = ionotify(fd, _NOTIFY_ACTION_POLLARM, _NOTIFY_COND_INPUT, &ev);
        if (now == -1) { perror("ionotify"); return 1; }
        if (!(now & _NOTIFY_COND_INPUT)) {
            struct _pulse p;
            if (MsgReceivePulse(chid, &p, sizeof p, NULL) == -1) { perror("MsgReceivePulse"); return 1; }
            if (p.code != PULSE_CODE) continue;
        }
        print_value(fd);                         /* reading clears the device's "unread" flag */
        seen++;
    }
    return 0;
}
