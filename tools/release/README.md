# Release helper tools

## Generate z-stream release updates

Use `generate-zstream-release-updates.py` to update the Fleet examples Day 2
manifests and release artifact lists from the SUSE Edge release manifest. The
release manifest is treated as the source of truth and must provide:

- `release_manifest.yaml`
- `release_images.yaml`
- `tooling_manifest.yaml`

Dry-run against a release manifest container from the Factory/main OBS project:

```bash
./tools/release/generate-zstream-release-updates.py \
  --container-image registry.opensuse.org/isv/suse/edge/factory/test_manifest_images/release-manifest:3.6.0
```

Dry-run against a release manifest container from a release branch OBS project:

```bash
./tools/release/generate-zstream-release-updates.py \
  --container-image registry.opensuse.org/isv/suse/edge/3.5/test_manifest_images/3.5/release-manifest:3.5.2
```

With `--container-image`, the script uses `podman` or `docker` to pull the image
if it is not already available locally, then copies the manifest files out of
the container image. Use `--skip-pull` if the image must already exist locally.

Dry-run against locally extracted Factory files, useful during development:

```bash
./tools/release/generate-zstream-release-updates.py \
  --manifest-dir ../Factory/release-manifest-image
```

Dry-run against a specific Factory release branch/ref:

```bash
./tools/release/generate-zstream-release-updates.py \
  --factory-repo ../Factory \
  --factory-ref upstream/3.6.1
```

Dry-run against a raw `release_manifest.yaml` URL:

```bash
./tools/release/generate-zstream-release-updates.py \
  --manifest-url https://src.opensuse.org/suse-edge/Factory/raw/branch/3.6.1/release-manifest-image/release_manifest.yaml
```

When using `--manifest-url`, `release_images.yaml` and `tooling_manifest.yaml`
are downloaded from the same URL directory.

Write the generated updates from a release manifest container:

```bash
./tools/release/generate-zstream-release-updates.py \
  --container-image registry.opensuse.org/isv/suse/edge/3.5/test_manifest_images/3.5/release-manifest:3.5.2 \
  --write
```

CI/check mode exits non-zero if the generated content differs from the current
repository state:

```bash
./tools/release/generate-zstream-release-updates.py \
  --container-image registry.opensuse.org/isv/suse/edge/3.5/test_manifest_images/3.5/release-manifest:3.5.2 \
  --check
```

The script updates GitRepo revisions, Day 2 upgrade plans, chart template
versions, EIB/kubectl/release-manifest entries in `edge-release-images.txt`,
RKE2 downloadable artifacts, and the Helm OCI artifact list.

The release version is read from `spec.releaseVersion` and validated against
`metadata.name`, for example `release-manifest-3-6-1` implies `3.6.1`. This is
intentional because each z-stream release is prepared on its own Factory branch.
