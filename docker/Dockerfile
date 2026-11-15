# Linux/amd64 host image for the QNX SDP 8.0 command-line tools (qcc, mkifs, secpolcompile, mkqnximage).
# The SDP itself is NOT in the image: it is installed on the host with QNX Software Center and bind-mounted
# at the same absolute path (see scripts/qcc.sh). SDP 8.0 ships no macOS host tools, so on Apple-Silicon Macs
# this image runs under Docker's amd64 emulation.
FROM ubuntu:22.04
ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates file make python3 procps \
    && rm -rf /var/lib/apt/lists/*
