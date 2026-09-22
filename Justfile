# justfile for eib.
#
# Wraps the Containerfile and the eib CLI into a few recipes so "run the
# pipeline" is one command. Requires `just` and rootful `podman`.
#
# The host's build_dir is mounted at the same path in the container, so
# the default --workspace (/var/tmp/build/eib-workspace) and its build
# output survive between runs, and import-image can find what
# build-image built.
#
# Quick start:
#   just build-container                                        # build the eib container image
#   just build-image image build --config config/config.yml            # assemble the workspace and build
#   just build-image image build --config config/config.yml --dry-run  # see what would happen first
#   just import-image --config config/config.yml                       # import the built OCI image into podman

image_name := "eib"
build_dir := "/var/tmp/build"

# List available recipes
default:
    @just --list

# Build the eib container image (mkosi + PyYAML bundled)
build-container:
    podman build -t {{ image_name }} -f Containerfile .

# Run eib with arbitrary arguments (e.g. `just build-image image build --config config.yml`)
build-image *args: build-container
    mkdir -p {{ build_dir }}
    podman run --rm --privileged \
        -v "$(pwd)":/workspace -w /workspace \
        -v {{ build_dir }}:{{ build_dir }} \
        {{ image_name }} {{ args }}

# The host's image store is mounted over the container's, so skopeo
# writes straight into it.
#
# Import the built OCI image into the host's podman storage (e.g. `just import-image --config config.yml`)
import-image *args: build-container
    mkdir -p {{ build_dir }}
    podman run --rm --privileged \
        -v "$(pwd)":/workspace -w /workspace \
        -v {{ build_dir }}:{{ build_dir }} \
        -v /var/lib/containers/storage:/var/lib/containers/storage \
        {{ image_name }} image import {{ args }}

# Remove the built container image
clean:
    podman rmi {{ image_name }} 2>/dev/null || true
