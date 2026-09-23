image_name := "eib"

# List available recipes
default:
    @just --list

# Build the eib container image (mkosi + PyYAML bundled)
build-container:
    sudo podman build -t {{ image_name }} -f Containerfile .

