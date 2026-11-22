# How Telltale differs from nearby QNX submissions

Telltale checks one thing: whether the number and lamps on an instrument cluster's glass match the vehicle's true state, by reading the composed display back through QNX Screen. These are the closest other public QNX 8.0 projects we found on Devpost (from their public pages and taglines; we did not run them), and the exact boundary with ours.

| Project | What it does (summarised from its Devpost page) | Boundary with Telltale |
|---|---|---|
| RearGuard | Keeps a rear-camera feed alive as its own QNX process, timed from the screen by a separate probe | Isolating the camera keeps the feed up when infotainment crashes or updates; its probe also reads the composed display to time when a camera frame appears, but it does not compare the value shown with vehicle state. Telltale decodes digits and lamps from the pixels and compares them with the vehicle state. |
| SafeDash | Fail-safe digital cockpit and CAN-FD broker using adaptive partitioning and watchdog supervision | A watchdog supervises a CAN heartbeat; its page does not describe reading the display. Telltale catches a live renderer that draws a wrong number (MISDRAW, STALE), which a heartbeat cannot see. |
| Vigil | Bedside alarm engine that never shows a stale number as live | Same concern in a medical setting, handled in the data path. Telltale checks the rendered pixels, after the renderer. |
| PHANToM GRID | Automotive motion engine on QNX | Not a display check. |
| cellguard | Stops a lab robot arm safely when a driver crashes or hangs | Supervises a driver and recovers a process; Telltale supervises what the display shows. |
| qnxprobe, kevscope | A host static scanner for C and a host trace analyser, used before or after a run | They run on the host; Telltale runs on the QNX target and acts on a live display. |
| knotwatch | Watches a running QNX 8.0 system and reports message-passing deadlocks from thread and connection state | It runs on the target too, but it reports deadlocks; it does not read what the display shows. |

Not covered by Telltale: the physical path from the display controller to the panel, display-controller CRC/signature units (a different stage), and a mirrored display (per the QNX `screen_read_display` documentation it returns ENOTSUP). Timings are QEMU (TCG), not hardware. See [DEMO.md](../DEMO.md) and [ENGINEERING.md](ENGINEERING.md).
