/* Telltale: vehicle state as published by vehicled (/dev/vehicle).
 * Shared by vehicled, vehfeed, cluster and the telltale monitor.
 * SPDX-License-Identifier: Apache-2.0 */
#ifndef TELLTALE_VEHICLE_H
#define TELLTALE_VEHICLE_H

#include <stdint.h>

enum veh_gear { GEAR_P = 0, GEAR_R = 1, GEAR_N = 2, GEAR_D = 3 };

/* lamp bits in veh_state.lamps */
#define LAMP_TURN_L  (1u << 0)
#define LAMP_TURN_R  (1u << 1)
#define LAMP_BRAKE   (1u << 2)
#define LAMP_ABS     (1u << 3)
#define LAMP_BELT    (1u << 4)
#define LAMP_HIGH    (1u << 5)

struct veh_state {
    uint64_t seq;        /* monotonically increasing per accepted sample */
    uint64_t t_ns;       /* CLOCK_MONOTONIC when vehicled accepted it */
    float    trace_s;    /* position in the replayed trace (s) */
    float    speed_kmh;  /* CAN speed (m/s * 3.6) */
    uint8_t  gear;       /* enum veh_gear */
    uint8_t  lamps;      /* LAMP_* bits */
};

#endif
