# P1a spike run `20261115T1203Z` — QEMU (TCG), x86_64 QSTI guest

| gate | verdict |
|---|---|
| G2 | PASS |
| G1 | PASS (G1-FULL) |
| S-OCC | PASS |
| S-UID | PASS |
| G4 | PASS |
| G3 (minimal) | PASS |


## G2 — qcc-built binary runs on the target

```
TT-G2 hello from QNX 8.0.0 2026/06/05-16:26:03EDT x86pc, cpu=4, clock_res_ns=1000000
```

## G1 — Screen read-back

| route | tag | uid | status | mismatching px | read ms |
|---|---|---|---|---|---|
| display | plain | 0 | EXACT | 0 | 6.72 |
| window | plain | 0 | EXACT | 0 | 2.25 |
| display | covered | 0 | DIFF | 40000 | 2.87 |
| window | covered | 0 | EXACT | 0 | 2.13 |
| display | uid1000-app | 1000 | FAIL | 13 | — |
| window | uid1000-app | 1000 | EXACT | 0 | 2.66 |
| display | uid1001-app | 1001 | FAIL | 13 | — |
| window | uid1001-app | 1001 | EXACT | 0 | 1.90 |
| ctx | uid1000-wm | 1000 | FAIL | 13 | — |

Composition check: the top-z cover over the right half changed 40000 of 80000 px in the display read (expected 40000 = the covered half); the window read under the cover stayed EXACT.

## G4 — timing on this QEMU (TCG) target

| measure | n | p50 ms | p95 ms | max ms | bar |
|---|---|---|---|---|---|
| 20 ms timer pulse, abs error | 5000 | 1.28 | 4.60 | 20.99 | p95 ≤ 5 |
| one check = 3 × screen_read_display | 500 | 2.62 | 5.85 | 7.96 | p95 ≤ 10 |
| — lamp row 1280×64 | 500 | 0.92 | 2.59 | 5.64 | |
| — digit block 360×336 | 500 | 1.11 | 2.74 | 5.76 | |
| — tick 8×8 | 500 | 0.28 | 0.92 | 5.13 | |

## G3 — secpol least privilege (minimal: types + channel connect + uid drop)

```
policy id:0
policy id:e61229a5 (immutable)
server pid=1753120 uid=1000 type=vehicle_t(id 2) chid=1
server pid=1761313 uid=1000 type=render_t(id 3) chid=1
server pid=1773603 uid=0 type=default(id 0) chid=1
-- monitor_t
client uid=1000 type=monitor_t(id 1) -> /tmp/tt/v.chan: ConnectAttach OK, MsgSend rc=0 reply='ok'
client uid=1000 type=monitor_t(id 1) -> /tmp/tt/r.chan: ConnectAttach DENIED errno=1 (Operation not permitted)
client uid=1000 type=monitor_t(id 1) -> /tmp/tt/d.chan: ConnectAttach OK, MsgSend rc=0 reply='ok'
-- render_t
client uid=1000 type=render_t(id 3) -> /tmp/tt/v.chan: ConnectAttach OK, MsgSend rc=0 reply='ok'
client uid=1000 type=render_t(id 3) -> /tmp/tt/r.chan: ConnectAttach DENIED errno=1 (Operation not permitted)
-- untyped uid 1000
client uid=1000 type=default(id 0) -> /tmp/tt/v.chan: ConnectAttach DENIED errno=1 (Operation not permitted)
client uid=1000 type=default(id 0) -> /tmp/tt/r.chan: ConnectAttach DENIED errno=1 (Operation not permitted)
client uid=1000 type=default(id 0) -> /tmp/tt/d.chan: ConnectAttach OK, MsgSend rc=0 reply='ok'
```

