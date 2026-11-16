/* counter.h - the contract between the /dev/counter resource manager and its clients. */
#ifndef COUNTER_H
#define COUNTER_H

#include <stdint.h>
#include <devctl.h>

#define COUNTER_DEVPATH "/dev/counter"

/* _DCMD_MISC is the devctl class QNX recommends for non-QNX drivers. The command numbers are ours. */
#define DCMD_COUNTER_GET   __DIOF(_DCMD_MISC, 0x40, uint64_t) /* device -> client: current value */
#define DCMD_COUNTER_ADD   __DIOT(_DCMD_MISC, 0x41, int32_t)  /* client -> device: add a delta   */
#define DCMD_COUNTER_RESET __DION(_DCMD_MISC, 0x42)           /* no data: set the value to 0     */

#endif
