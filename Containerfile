# Containerfile for eib.
#
# Bundles mkosi (and its sandboxing dependency, bubblewrap) plus PyYAML,
# which eib.py needs to parse the workspace config. That's the whole
# pipeline now: assemble a workspace, then `mkosi build`.
#
# mkosi needs privileges this container doesn't get by default (loop
# devices, bubblewrap sandboxing). Run with at least --privileged and the
# project directory mounted in -- see the justfile, which wraps the exact
# `podman run` invocation.

FROM debian:trixie-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        mkosi \
        bubblewrap \
        ca-certificates \
        python3 \
        python3-yaml \
    && rm -rf /var/lib/apt/lists/*

COPY eib.py /usr/local/bin/eib
RUN chmod +x /usr/local/bin/eib

WORKDIR /workspace
ENTRYPOINT ["eib"]
CMD ["--help"]
