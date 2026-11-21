# Demo-video evidence (QNX 8.0 guest, QEMU TCG x86_64)

Every product frame in the demo video was read back from QNX Screen on the guest by `ttrec`
(`src/tools/ttrec.c`, the regions that can change, ~30 fps) during a scripted fault run (`scripts/scenario.sh` with
`TT_REC`). Nothing is redrawn. One folder per take used in the video, with the run's logs; `numbers.txt` in each folder
re-derives that take's on-screen milliseconds with `tools/video_numbers.py` (the bench's fault stamp from
`cluster-bench.csv`, rounded to 0.1 ms). All timing is QEMU (TCG) timing, not hardware timing.

**Take gate:** a take is used only if it has exactly the injected incident (0 for a monitor-off take) in both
`telltale.log` (DETECT lines) and `incidents.csv`. Takes that failed are kept in `discarded/` with the reason.

| take | used for | monitor | incidents | on screen, ms after the fault |
|---|---|---|---|---|
| `20261121T124518Z-freeze` | FREEZE cold open, right pane + slow motion | on (engineering view) | 1 TICK_STALL | last true frame +101.3 · caught +123.4 · safe layer +181.3 · true frame +580.7 |
| `20261121T124611Z-freeze` | FREEZE cold open, left pane; monitor-off ablation shot | disarmed | 0 | the glass keeps showing 48 |
| `20261121T130116Z-stale` | STALE | on | 1 VALUE_MISMATCH | caught +136.2 · safe layer +207.0 · restart +325.3 · true frame +652.9 |
| `20261121T130309Z-misdraw` | MISDRAW, right pane | on | 1 VALUE_MISMATCH | caught +9.7 · safe layer +38.3 |
| `20261121T135734Z-misdraw` | MISDRAW, left pane | disarmed | 0 | reversed digits stay on the glass |
| `20261121T135638Z-crash` | CRASH (renderer SIGSEGV) | on | 1 BLANK, cause crash | caught +24.8 · safe layer +68.6 · new renderer +246.3 · true frame +315.4 |

`terminal/` holds the three terminal sessions shown in the video as plain text, recorded live on the same guest:
`vehicle.txt` (`cat /dev/vehicle`), `pidin.txt` (thread priorities, telltale at 60, FIFO) and `secpol.txt`
(`tests/target/secpol_denies.sh` with the policy loaded, 22/22 passed; the video plays this 22.9 s run in 6.3 s and
labels it on screen: sped up ×3.6).

Bench figures in the video (p50/p95 per class, 200 faults, the ablation and the false-positive run) come from
`evidence/RESULTS.md`, not from these takes.
