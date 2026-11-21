# How Telltale differs from nearby QNX submissions

Telltale checks one thing: whether the number and lamps on an instrument cluster's glass match the vehicle's true state, by reading the composed display back through QNX Screen. These are the closest other public QNX 8.0 projects we found on Devpost (from their public pages and taglines; we did not run them), and the exact boundary with ours.

| Project | What it does (its own tagline) | Boundary with Telltale |
|---|---|---|
| RearGuard | Keeps a rear-camera feed alive as its own QNX process, timed from the screen by a separate probe | Isolating a feed prevents a dead feed; it does not say whether the glass shows a wrong value. Telltale decodes digits and lamps from the pixels and compares them with the vehicle state. |
| SafeDash | Fail-safe digital cockpit and CAN-FD broker using adaptive partitioning and watchdog supervision | A watchdog supervises a process; it does not read what is displayed. Telltale catches a live renderer that draws a wrong number (MISDRAW, STALE), which a heartbeat cannot see. |
| Vigil | Bedside alarm engine that never shows a stale number as live | Same concern in a medical setting, handled in the data path. Telltale checks the rendered pixels, after the renderer. |
| PHANToM GRID | Automotive motion engine on QNX | Not a display check; Telltale makes no ISO 26262 / ASIL claim. |
| cellguard | Stops a lab robot arm safely when a driver crashes or hangs | Supervises a driver and recovers a process; Telltale supervises what the display shows. |
| Host-side QNX linters and trace viewers (e.g. qnxprobe, knotwatch, kevscope) | Analyse code or traces before or after a run | They run on the host; Telltale runs on the QNX target and acts on a live display. |

Not covered by Telltale: the physical path from the display controller to the panel, display-controller CRC/signature units (a different stage), and a mirrored display (`screen_read_display` returns ENOTSUP). Timings are QEMU (TCG), not hardware. See [DEMO.md](../DEMO.md) and [ENGINEERING.md](ENGINEERING.md).
