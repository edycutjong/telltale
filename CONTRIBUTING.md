# Contributing

## Host checks (what CI runs)
```
make lint        # ruff
make test-host   # C unit tests (host cc) + Python tests
```

## Target checks (need the QNX SDP, run locally)
`make test-target` builds with `qcc` and runs the suite on a QEMU guest. CI does not run it:
the QNX SDP 8.0 can only be downloaded behind a myQNX login and a license, which cannot be
supplied to a public CI runner. Target results are committed under `evidence/test-target/`.

## Pull requests
Small commits, tests with every change, no invented numbers: any figure in the docs must trace to
`evidence/`. Never commit credentials; `gitleaks` runs on every push.
