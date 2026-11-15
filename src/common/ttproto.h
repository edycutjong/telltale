/* Telltale: resource-manager paths and devctl codes for /dev/vehicle, /dev/telltale, /dev/cluster-ctl.
 * SPDX-License-Identifier: Apache-2.0 */
#ifndef TELLTALE_PROTO_H
#define TELLTALE_PROTO_H
#include <stdint.h>
#include "vehicle.h"
#ifdef __QNX__
#include <devctl.h>
#else
#define _DCMD_MISC 0x05
#define __DIOF(c, n, t) (((c) << 8) | (n))
#define __DIOT(c, n, t) (((c) << 8) | (n))
#define __DIOTF(c, n, t) (((c) << 8) | (n))
#define __DION(c, n) (((c) << 8) | (n))
#endif

#define PATH_VEHICLE  "/dev/vehicle"
#define PATH_TELLTALE "/dev/telltale"
#define PATH_EVENTS   "/dev/telltale-events"
#define PATH_CLCTL    "/dev/cluster-ctl"

#define VEH_RING 64
struct veh_since {             /* DCMD_VEH_GET_SINCE: in = after_seq, out = n samples with seq > after_seq */
    uint64_t after_seq;
    uint32_t n;
    uint32_t pad;
    struct veh_state s[VEH_RING];
};
#define DCMD_VEH_GET       __DIOF(_DCMD_MISC, 0x51, struct veh_state)
#define DCMD_VEH_GET_SINCE __DIOTF(_DCMD_MISC, 0x52, struct veh_since)
#define DCMD_VEH_SET       __DIOT(_DCMD_MISC, 0x53, struct veh_state)

struct tt_register {           /* DCMD_TT_REGISTER: the renderer announces itself after every (re)start */
    int32_t pid;
    int32_t draw_tid;          /* thread the cause check samples */
    char    win_id[32];        /* SCREEN_PROPERTY_ID_STRING of the cluster window */
};
#define DCMD_TT_REGISTER   __DIOT(_DCMD_MISC, 0x61, struct tt_register)
#define DCMD_TT_ARM        __DION(_DCMD_MISC, 0x62)
#define DCMD_TT_DISARM     __DION(_DCMD_MISC, 0x63)  /* observe-only: no safe layer, kill or incident */
#define DCMD_TT_SET_PERIOD __DIOT(_DCMD_MISC, 0x64, int32_t)
#define DCMD_TT_REARM      __DION(_DCMD_MISC, 0x65)  /* reset the restart cap / leave GIVEUP */
#define DCMD_TT_DEBUG      __DIOT(_DCMD_MISC, 0x66, int32_t)

enum cl_fault { CL_NONE = 0, CL_FREEZE = 1, CL_BLANK = 2, CL_STALE = 3, CL_MISDRAW = 4, CL_CRASH = 5, CL_HEAL = 6, CL_LAMPDROP = 7 };
struct cl_fault_cmd { int32_t cls; int32_t arg; uint64_t run_id; };
#define DCMD_CL_FAULT      __DIOT(_DCMD_MISC, 0x71, struct cl_fault_cmd)

#endif
