/* counterctl.c - command-line client for /dev/counter using devctl().
 *   counterctl get | add <N> | reset
 */
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include "counter.h"

int main(int argc, char **argv)
{
    int fd = open(COUNTER_DEVPATH, O_RDWR);
    if (fd == -1) { perror("open " COUNTER_DEVPATH); return 1; }

    int rc = 0;
    if (argc == 2 && !strcmp(argv[1], "get")) {
        uint64_t v;
        if ((rc = devctl(fd, DCMD_COUNTER_GET, &v, sizeof v, NULL)) == 0) printf("%llu\n", (unsigned long long)v);
    } else if (argc == 3 && !strcmp(argv[1], "add")) {
        int32_t d = (int32_t)atoi(argv[2]);
        rc = devctl(fd, DCMD_COUNTER_ADD, &d, sizeof d, NULL);
    } else if (argc == 2 && !strcmp(argv[1], "reset")) {
        rc = devctl(fd, DCMD_COUNTER_RESET, NULL, 0, NULL);
    } else {
        fprintf(stderr, "usage: counterctl get | add <N> | reset\n");
        return 2;
    }
    if (rc) { fprintf(stderr, "devctl: %s\n", strerror(rc)); return 1; }   /* devctl() returns the errno itself */
    return 0;
}
