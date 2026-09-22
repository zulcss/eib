# justfile for eib.
#
# Wraps the Containerfile and the eib CLI into a few recipes so "run the
# pipeline" is one command. Requires `just` and `podman`.
#
# Quick start:
#   just build-container                                        # build the eib container image
#   just build-image image build --config config/config.yml            # assemble the workspace and build
#   just build-image image build --config config/config.yml --dry-run  # see what would happen first

image_name := "eib"

# List available recipes
default:
    @just --list

# Build the eib container image (mkosi + PyYAML bundled)
build-container:
    podman build -t {{ image_name }} -f Containerfile .

# Run eib with arbitrary arguments (e.g. `just build-image image build --config eib.yaml`)
build-image *args: build-container
    podman run --rm --privileged \
        -v "$(pwd)":/workspace -w /workspace \
        {{ image_name }} {{ args }}

# Remove the built container image
clean:
    podman rmi {{ image_name }} 2>/dev/null || true
