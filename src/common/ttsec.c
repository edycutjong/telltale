/* SPDX-License-Identifier: Apache-2.0 */
#include <errno.h>
#include <pthread.h>
#include <sched.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/neutrino.h>
#include <secpol/secpol.h>
#include "ttsec.h"

static secpol_file_t *pol;

void *tt_policy_handle(void) { return pol; }

static void enter(const char *prog, int policy, int prio, int drop);
void tt_enter(const char *prog, int policy, int prio) { enter(prog, policy, prio, 1); }
void tt_enter_keep_uid(const char *prog, int policy, int prio) { enter(prog, policy, prio, 0); }

void tt_drop_uid(const char *prog)
{
    const char *u = getenv("TT_UID");
    if (u && *u) {
        int uid = atoi(u);
        if (setgid(uid) != 0 || setuid(uid) != 0) { fprintf(stderr, "%s: drop to uid %d: %s\n", prog, uid, strerror(errno)); exit(2); }
        printf("%s: now uid=%d\n", prog, getuid()); fflush(stdout);
    }
}

static void enter(const char *prog, int policy, int prio, int drop)
{
    if (prio > 0) {
        struct sched_param sp = {.sched_priority = prio};
        int rc = pthread_setschedparam(pthread_self(), policy, &sp);
        if (rc) { fprintf(stderr, "%s: setschedparam(%d): %s\n", prog, prio, strerror(rc)); exit(2); }
    }
    const char *type = getenv("TT_TYPE");
    if (type && *type) {
        const char *path = getenv("TT_POL") ? getenv("TT_POL") : "/tmp/tt/telltale.bin";
        pol = secpol_open(path, 0);
        if (!pol) { fprintf(stderr, "%s: secpol_open(%s): %s\n", prog, path, strerror(errno)); exit(2); }
        if (secpol_transition_type(pol, type, SECPOL_TYPE_NAME) != 0) {
            fprintf(stderr, "%s: transition to %s: %s\n", prog, type, strerror(errno)); exit(2);
        }
    }
    if (drop) {
        const char *u = getenv("TT_UID");
        if (u && *u) {
            int uid = atoi(u);
            if (setgid(uid) != 0 || setuid(uid) != 0) { fprintf(stderr, "%s: drop to uid %d: %s\n", prog, uid, strerror(errno)); exit(2); }
        }
    }
    printf("%s: pid=%d uid=%d type=%s prio=%d\n", prog, getpid(), getuid(), tt_type_name(), prio);
    fflush(stdout);
}

const char *tt_type_name(void)
{
    static char buf[64];
    uint32_t id = secpol_type_id();
    const char *n = pol ? secpol_get_type_name(pol, id) : NULL;
    if (!pol && id == 0) return "untyped";
    snprintf(buf, sizeof buf, "%s", n ? n : "?");
    return buf;
}

int tt_client_euid(int scoid)
{
    struct _client_info ci;
    if (ConnectClientInfo(scoid, &ci, 0) == -1) return -1;
    return (int)ci.cred.euid;
}

int tt_policy_loaded(void)
{
    uint32_t id = 0;
    return secpol_get_policy_id(&id) == 0 && id != 0;
}
