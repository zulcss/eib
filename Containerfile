# Containerfile for eib.
#
# Bundles mkosi plus PyYAML, which eib.py needs to parse the workspace
# config. That's the whole pipeline now: assemble a workspace, then
# `mkosi build`.
#
# Rather than apt's mkosi package (which lags upstream), mkosi is
# installed via pip straight from the eLxr fork's git repository, per
# mkosi's own recommended install method. Its runtime dependencies --
# the packages Debian's mkosi package would otherwise pull in -- are
# installed directly via apt instead. mkosi no longer shells out to
# bubblewrap for sandboxing (it bundles its own mkosi-sandbox tool), so
# that's dropped.
#
# eib itself is installed from pyproject.toml via pip, using the
# already-apt-installed setuptools and PyYAML (--no-build-isolation
# --no-deps keeps that install from reaching out to PyPI).
#
# mkosi needs privileges this container doesn't get by default (loop
# devices, sandboxing). Run with at least --privileged and the project
# directory mounted in -- see the justfile, which wraps the exact
# `podman run` invocation.

FROM debian:trixie-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        ca-certificates \
        python3 \
        python3-pip \
        python3-setuptools \
        python3-yaml \
        python3-cryptography \
        python3-pefile \
        systemd \
        systemd-boot-efi \
        systemd-boot-tools \
        systemd-container \
        systemd-repart \
        systemd-ukify \
        fdisk \
        debian-archive-keyring \
        cryptsetup-bin \
        dosfstools \
        mtools \
        cpio \
        kmod \
        btrfs-progs \
        e2fsprogs \
        erofs-utils \
        squashfs-tools \
        xz-utils \
        zstd \
        gnupg \
        pesign \
        tpm2-tools \
        efitools \
        openssl \
        jq \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --break-system-packages \
        "git+https://gitlab.com/elxr/tools/d2o/mkosi.git@main"

COPY pyproject.toml README.md eib.py /src/
RUN pip install --break-system-packages --no-build-isolation --no-deps /src \
    && rm -rf /src

WORKDIR /workspace
ENTRYPOINT ["eib"]
CMD ["--help"]
