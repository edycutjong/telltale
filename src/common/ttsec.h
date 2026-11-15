/* Telltale: process start-up -- scheduling, secpol type, uid drop -- in that order.
 * SPDX-License-Identifier: Apache-2.0 */
#ifndef TELLTALE_TTSEC_H
#define TELLTALE_TTSEC_H
#include <stdint.h>
#include <time.h>
#include <sched.h>
/* Set the calling thread's policy/priority (as root), then enter the secpol type named by $TT_TYPE
 * (policy file $TT_POL, default /tmp/tt/telltale.bin) and drop to $TT_UID if set.
 * Without $TT_TYPE the process stays untyped (no policy loaded). Exits on any failure. */
void tt_enter(const char *prog, int policy, int prio);
/* tt_enter without the uid drop; call tt_drop_uid() once the paths are attached */
void tt_enter_keep_uid(const char *prog, int policy, int prio);
void tt_drop_uid(const char *prog);
/* name of the caller's secpol type, or "untyped" */
const char *tt_type_name(void);
/* 1 if a security policy is loaded (secpol policy id != 0) */
int tt_policy_loaded(void);
void *tt_policy_handle(void);
/* effective uid of the client behind a resmgr message (scoid), or -1 */
int tt_client_euid(int scoid);
/* write all n bytes (retries short writes and EINTR); 0 on success, -1 on error */
int tt_write_all(int fd, const void *buf, size_t n);
static inline uint64_t tt_now_ns(void)
{
    struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t);
    return (uint64_t)t.tv_sec * 1000000000ull + (uint64_t)t.tv_nsec;
}
#endif
