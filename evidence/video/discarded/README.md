# Discarded video takes (kept, not hidden)

A take is used in the demo video only if it has exactly the injected incident and nothing else (`telltale.log` DETECT
lines and `incidents.csv` rows; 0 for a monitor-off take). These takes failed that gate. All ran on the same QEMU (TCG)
guest on 2026-11-21; host load average = `sysctl vm.loadavg` on the Mac running QEMU.

| take | incidents | why it failed | what changed |
|---|---|---|---|
| 20261121T122607Z-freeze | 12 | TICK_STALL from the steady hold onward: host load average ~150-180 (other jobs on the Mac) starved the TCG guest, and the first `ttrec` read the whole display at 30 fps | `ttrec` reads only the regions that can change; takes wait for a quiet host |
| 20261121T124407Z-freeze | 6 | false TICK_STALLs before the fault: `TT_DEBUG=1` starts three ttshot watcher loops that spawn `grep` every 50 ms on the guest | `TT_DEBUG=2`: engineering view without the watcher loops |
| 20261121T124844Z-stale | 2 | one TICK_STALL before the fault; host load rose to 92 during the run | retaken at load 10 |
| 20261121T130207Z-misdraw | 2 | the injected MISDRAW (caught, 19.7 ms) plus a later VALUE_MISMATCH 37 vs 35: the respawned renderer lagged at host load 24 | retaken at load 20 |
| 20261121T130400Z-crash | 2 | one TICK_STALL before the crash at rising host load | retaken |
| 20261121T130510Z-crash | 9 | the CRASH incident, then 8 self-resolved TICK_STALLs 16.8-24.7 s after the fault, during log collection at host load 47 (`telltale.log` was copied before them, so the first gate missed them) | the gate also counts `incidents.csv` rows |

The bench numbers in `evidence/RESULTS.md` are unaffected: they come from the bench runs, not from these takes. Under a
starved emulator the monitor does what it is built to do: a tick that really stops for more than `tick_stall_ms` is
reported, and these reports self-resolved without a restart.
