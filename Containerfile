# Containerfile for eib.
#
# Bundles mkosi (and its sandboxing dependency, bubblewrap) plus PyYAML,
# which eib.py needs to parse the workspace config. That's the whole
# pipeline now: assemble a workspace, then `mkosi build`.
#
# eib itself is installed from pyproject.toml via pip, using the
# already-apt-installed setuptools and PyYAML (--no-build-isolation
# --no-deps keeps that install from reaching out to PyPI).
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
        python3-pip \
        python3-setuptools \
        python3-yaml \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md eib.py /src/
RUN pip install --break-system-packages --no-build-isolation --no-deps /src \
    && rm -rf /src

WORKDIR /workspace
ENTRYPOINT ["eib"]
CMD ["--help"]
