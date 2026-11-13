# Telltale

An isolated QNX 8.0 monitor that reads back what the instrument cluster actually shows and checks it against the vehicle state.

**Status: early build.** This repository is being written during the QNX hackathon (Nov–Dec 2026). The QNX target code
(resource manager, Screen renderer, monitor, fault injector) has not landed yet; this README will describe it once it runs
on the QNX target.

## What is here today
| Path | What |
|---|---|
| `src/common/` | shared C headers: vehicle state layout, the `disp_kmh()` rounding rule the renderer and the monitor both use |
| `scripts/bench.py` | host-side analysis of bench CSVs from the target: per-class p50/p95/max, misses, failed rows, false positives |
| `tests/host/` | host-runnable unit tests (C + Python); `tests/fixtures/synthetic-*.csv` are hand-written test inputs, not measurements |

## Run the host tests
```sh
make test-host   # C tests with any POSIX cc, Python tests with Python 3.11+
make lint        # ruff
```

## License
Apache-2.0, see [LICENSE](LICENSE).
