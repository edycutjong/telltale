/* ttctl: control the telltale monitor over /dev/telltale devctls.
 *   ttctl status | arm | disarm | rearm | period <ms> | debug on|off
 * disarm = observe-only (read-back, decode and per-check rows continue; no safe layer, kill or incident).
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
    const char *cmd = argc > 1 ? argv[1] : "status";
    if (strcmp(cmd, "status")) tt_enter("ttctl", 0, 0);   /* control commands run in $TT_TYPE (injector_t) */
    int fd = open(PATH_TELLTALE, O_RDWR);
    if (fd < 0) { perror(PATH_TELLTALE); return 2; }
    if (!strcmp(cmd, "status")) {
        char buf[1024]; ssize_t n;
        while ((n = read(fd, buf, sizeof buf)) > 0) fwrite(buf, 1, (size_t)n, stdout);
        return 0;
    }
    int rc, v = 0;
    if (!strcmp(cmd, "arm")) rc = devctl(fd, DCMD_TT_ARM, NULL, 0, NULL);
    else if (!strcmp(cmd, "disarm")) rc = devctl(fd, DCMD_TT_DISARM, NULL, 0, NULL);
    else if (!strcmp(cmd, "rearm")) rc = devctl(fd, DCMD_TT_REARM, NULL, 0, NULL);
    else if (!strcmp(cmd, "period") && argc > 2) { v = atoi(argv[2]); rc = devctl(fd, DCMD_TT_SET_PERIOD, &v, sizeof v, NULL); }
    else if (!strcmp(cmd, "debug") && argc > 2) { v = !strcmp(argv[2], "on"); rc = devctl(fd, DCMD_TT_DEBUG, &v, sizeof v, NULL); }
    else { fprintf(stderr, "usage: ttctl status|arm|disarm|rearm|period <ms>|debug on|off\n"); return 1; }
    if (rc != EOK) { fprintf(stderr, "ttctl %s: %s\n", cmd, strerror(rc)); return 3; }
    printf("ttctl %s: ok\n", cmd);
    return 0;
}
