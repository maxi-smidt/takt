# Bundled Raspberry Pi release

CI (`.github/workflows/registry-image.yml`) drops exactly three files here
before building the Registry image, all produced by the same `package-pi`
job that publishes the GitHub Release:

- `takt-raspberry-pi-<version>.tar.gz` — the Pi install package
- `takt-raspberry-pi-<version>.tar.gz.sha256` — its checksum
- `takt-raspberry-pi-<version>.manifest.json` — version, commit, size, sha256

The `Dockerfile` copies this directory into the `registry` image at
`/opt/takt/bundled-release`. On startup the Registry
(`takt.registry.bundled_release.import_bundled_release`) verifies and
imports whatever it finds there into persistent storage, so a fresh
container already has its own version's Pi package available without any
manual upload.

This directory is empty in a normal checkout — that's fine, a local
`docker build --target registry .` just ships without a bundled release
(`bundled_release.status == "absent"`). Generated artifacts are gitignored;
only this README is tracked so the `COPY bundled-release/` step in the
Dockerfile always has a directory to copy.
