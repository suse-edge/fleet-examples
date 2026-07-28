#!/usr/bin/env python3
"""Generate Fleet examples updates for SUSE Edge z-stream releases.

The Factory release manifest is the source of truth.  This script consumes the
release manifest container, a Factory Git ref, a raw manifest URL, or an
extracted directory containing:

  - release_manifest.yaml
  - release_images.yaml
  - tooling_manifest.yaml

It then updates the Fleet examples Day 2 manifests and release artifact lists
without reformatting the YAML files wholesale.
"""

from __future__ import annotations

import argparse
import difflib
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin
from urllib.request import urlopen

try:
    import yaml
except ImportError:  # pragma: no cover - exercised only on missing dependency
    print(
        "error: this script requires PyYAML. Install it with 'python3 -m pip install PyYAML'.",
        file=sys.stderr,
    )
    sys.exit(2)


REQUIRED_MANIFEST_FILES = (
    "release_manifest.yaml",
    "release_images.yaml",
    "tooling_manifest.yaml",
)

RKE2_ARTIFACTS = (
    "sha256sum-amd64.txt",
    "rke2.linux-amd64.tar.gz",
    "rke2-images.linux-amd64.tar.zst",
    "rke2-images-multus.linux-amd64.tar.zst",
    "rke2-images-core.linux-amd64.tar.zst",
    "rke2-images-cilium.linux-amd64.tar.zst",
)


@dataclass(frozen=True)
class ChartInfo:
    release_name: str
    chart: str
    version: str
    repository: str | None = None


@dataclass(frozen=True)
class ReleaseData:
    release_version: str
    release_family: str
    rke2_version: str
    k3s_version: str
    kubernetes_version: str
    release_revision: str
    charts_by_release_name: dict[str, ChartInfo]
    oci_charts_by_path: dict[str, str]
    ordered_oci_chart_paths: list[str]
    edge_images: list[str]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate Fleet examples updates from SUSE Edge release manifest data.",
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--container-image",
        help=(
            "Release manifest container image, for example "
            "registry.opensuse.org/isv/suse/edge/3.5/test_manifest_images/3.5/release-manifest:3.5.2."
        ),
    )
    source.add_argument(
        "--manifest-dir",
        type=Path,
        help="Directory containing release_manifest.yaml, release_images.yaml and tooling_manifest.yaml.",
    )
    source.add_argument(
        "--manifest-url",
        help=(
            "Raw URL to release_manifest.yaml. release_images.yaml and tooling_manifest.yaml "
            "are downloaded from the same URL directory."
        ),
    )
    source.add_argument(
        "--factory-ref",
        help="Factory Git ref/branch that contains release-manifest-image, for example upstream/3.6.1.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true", help="Write changes to the repository.")
    mode.add_argument(
        "--check",
        action="store_true",
        help="Exit non-zero if generated updates differ from the current repository state.",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="fleet-examples repository root. Defaults to this script's repository.",
    )
    parser.add_argument(
        "--container-engine",
        choices=("podman", "docker"),
        help="Container engine used with --container-image. Defaults to podman, then docker.",
    )
    parser.add_argument(
        "--skip-pull",
        action="store_true",
        help="With --container-image, do not pull the image if it is missing locally.",
    )
    parser.add_argument(
        "--factory-repo",
        type=Path,
        help="Factory repository path. Required with --factory-ref.",
    )
    parser.add_argument(
        "--show-diff",
        action="store_true",
        help="Print unified diffs for files that would change.",
    )
    return parser.parse_args()


def load_yaml(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def find_container_engine(requested: str | None) -> str:
    candidates = [requested] if requested else ["podman", "docker"]
    for candidate in candidates:
        if candidate and shutil.which(candidate):
            return candidate
    wanted = requested or "podman or docker"
    raise RuntimeError(f"could not find container engine: {wanted}")


def ensure_container_image(image: str, container_engine: str, skip_pull: bool) -> None:
    if subprocess.call(
        [container_engine, "image", "inspect", image],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ) == 0:
        return

    if skip_pull:
        raise RuntimeError(f"container image is not available locally: {image}")

    print(f"Pulling release manifest image: {image}", file=sys.stderr)
    subprocess.check_call([container_engine, "pull", image])


def extract_manifest_container(image: str, engine: str | None, skip_pull: bool) -> tempfile.TemporaryDirectory[str]:
    temp_dir = tempfile.TemporaryDirectory(prefix="edge-release-manifest-")
    container_engine = find_container_engine(engine)
    container_id = ""
    try:
        ensure_container_image(image, container_engine, skip_pull)
        container_id = subprocess.check_output(
            [container_engine, "create", image],
            text=True,
            stderr=subprocess.STDOUT,
        ).strip()
        for filename in REQUIRED_MANIFEST_FILES:
            subprocess.check_call(
                [container_engine, "cp", f"{container_id}:/{filename}", str(Path(temp_dir.name) / filename)]
            )
    except subprocess.CalledProcessError as exc:
        temp_dir.cleanup()
        output = exc.output.strip() if isinstance(exc.output, str) else str(exc)
        raise RuntimeError(f"failed to extract release manifest files from {image}: {output}") from exc
    finally:
        if container_id:
            subprocess.call(
                [container_engine, "rm", "-f", container_id],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
    return temp_dir


def validate_manifest_dir(manifest_dir: Path) -> Path:
    missing = [name for name in REQUIRED_MANIFEST_FILES if not (manifest_dir / name).is_file()]
    if missing:
        raise RuntimeError(f"{manifest_dir} is missing required file(s): {', '.join(missing)}")
    return manifest_dir


def extract_manifest_url(release_manifest_url: str) -> tempfile.TemporaryDirectory[str]:
    if not release_manifest_url.endswith("/release_manifest.yaml"):
        raise RuntimeError("--manifest-url must point to a raw release_manifest.yaml file")

    temp_dir = tempfile.TemporaryDirectory(prefix="edge-release-manifest-url-")
    base_url = release_manifest_url.rsplit("/", 1)[0] + "/"
    try:
        for filename in REQUIRED_MANIFEST_FILES:
            url = urljoin(base_url, filename)
            destination = Path(temp_dir.name) / filename
            with urlopen(url) as response:
                destination.write_bytes(response.read())
    except OSError as exc:
        temp_dir.cleanup()
        raise RuntimeError(f"failed to download release manifest files from {base_url}: {exc}") from exc

    try:
        validate_manifest_dir(Path(temp_dir.name))
    except RuntimeError:
        temp_dir.cleanup()
        raise
    return temp_dir


def extract_factory_ref(factory_repo: Path, factory_ref: str) -> tempfile.TemporaryDirectory[str]:
    if not factory_repo.is_dir():
        raise RuntimeError(f"Factory repository does not exist: {factory_repo}")

    temp_dir = tempfile.TemporaryDirectory(prefix="edge-factory-manifest-")
    archive_path = Path(temp_dir.name) / "release-manifest-image.tar"
    try:
        subprocess.check_call(
            [
                "git",
                "-C",
                str(factory_repo),
                "archive",
                "--format=tar",
                "--output",
                str(archive_path),
                factory_ref,
                "release-manifest-image",
            ]
        )
        with tarfile.open(archive_path) as archive:
            archive.extractall(temp_dir.name)
        validate_manifest_dir(Path(temp_dir.name) / "release-manifest-image")
    except subprocess.CalledProcessError as exc:
        temp_dir.cleanup()
        raise RuntimeError(f"failed to archive release-manifest-image from {factory_repo} at {factory_ref}") from exc
    except (tarfile.TarError, RuntimeError) as exc:
        temp_dir.cleanup()
        raise RuntimeError(f"failed to extract release-manifest-image from {factory_repo} at {factory_ref}: {exc}") from exc
    return temp_dir


def chart_major_for(release_family: str) -> str:
    major, minor = release_family.split(".", 1)
    return str(int(major) * 100 + int(minor))


def kubernetes_version_from_rke2(rke2_version: str) -> str:
    match = re.match(r"^v?([^+]+)\+rke2r\d+$", rke2_version)
    if not match:
        raise RuntimeError(f"cannot derive Kubernetes version from RKE2 version: {rke2_version}")
    return match.group(1)


def release_version_from_metadata_name(release_manifest: dict[str, Any]) -> str | None:
    name = str(release_manifest.get("metadata", {}).get("name", ""))
    match = re.match(r"^release-manifest-(\d+)-(\d+)-(\d+)$", name)
    if not match:
        return None
    return ".".join(match.groups())


def release_version_from_manifest(release_manifest: dict[str, Any]) -> str:
    spec_version = release_manifest.get("spec", {}).get("releaseVersion")
    metadata_version = release_version_from_metadata_name(release_manifest)

    if spec_version is None and metadata_version is None:
        raise RuntimeError("cannot determine release version from spec.releaseVersion or metadata.name")

    if spec_version is None:
        return str(metadata_version)

    spec_version = str(spec_version)
    if metadata_version and metadata_version != spec_version:
        raise RuntimeError(
            f"release version mismatch: spec.releaseVersion is {spec_version}, "
            f"but metadata.name implies {metadata_version}"
        )
    return spec_version


def substitution_context(release_version: str) -> dict[str, str]:
    release_family = ".".join(release_version.split(".")[:2])
    return {
        "%%IMG_REPO%%": "registry.suse.com",
        "%%MANIFEST_REPO%%": "registry.suse.com",
        "%%IMG_PREFIX%%": f"edge/{release_family}/",
        "%%CHART_REPO%%": "oci://registry.suse.com",
        "%%CHART_PREFIX%%": "edge/charts/",
        "%%CHART_MAJOR%%": chart_major_for(release_family),
    }


def substitute_placeholders(value: str, context: dict[str, str]) -> str:
    result = value
    for placeholder, replacement in context.items():
        result = result.replace(placeholder, replacement)
    return result


def strip_registry(image: str, registry: str = "registry.suse.com") -> str:
    prefix = f"{registry}/"
    return image[len(prefix) :] if image.startswith(prefix) else image


def parse_release_image_names(path: Path) -> list[str]:
    images: list[str] = []
    pattern = re.compile(r"^\s*-\s*name:\s*(.+?)\s*$")
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            match = pattern.match(line)
            if not match:
                continue
            image = match.group(1).strip().strip('"').strip("'")
            if image:
                images.append(image)
    return images


def flatten_workload_charts(workloads: list[dict[str, Any]], context: dict[str, str]) -> list[ChartInfo]:
    charts: list[ChartInfo] = []

    def add_chart(raw_chart: dict[str, Any]) -> None:
        release_name = str(raw_chart.get("releaseName", "")).strip()
        chart = str(raw_chart.get("chart", "")).strip()
        version = str(raw_chart.get("version", "")).strip()
        repository = raw_chart.get("repository")
        if not release_name or not chart or not version:
            return
        charts.append(
            ChartInfo(
                release_name=release_name,
                chart=substitute_placeholders(chart, context),
                version=substitute_placeholders(version, context),
                repository=str(repository).strip() if repository else None,
            )
        )

    for workload in workloads:
        add_chart(workload)
        for key in ("dependencyCharts", "addonCharts"):
            for chart in workload.get(key, []) or []:
                add_chart(chart)

    return charts


def build_release_data(manifest_dir: Path) -> ReleaseData:
    release_manifest = load_yaml(manifest_dir / "release_manifest.yaml")
    tooling_manifest = load_yaml(manifest_dir / "tooling_manifest.yaml")
    spec = release_manifest["spec"]
    components = spec["components"]
    release_version = release_version_from_manifest(release_manifest)
    release_family = ".".join(release_version.split(".")[:2])
    context = substitution_context(release_version)

    rke2_version = str(components["kubernetes"]["rke2"]["version"])
    k3s_version = str(components["kubernetes"]["k3s"]["version"])
    kubernetes_version = kubernetes_version_from_rke2(rke2_version)

    workloads = components.get("workloads", {}).get("helm", []) or []
    ordered_charts = flatten_workload_charts(workloads, context)
    charts_by_release_name = {chart.release_name: chart for chart in ordered_charts}

    oci_charts_by_path: dict[str, str] = {}
    ordered_oci_chart_paths: list[str] = []
    for chart in ordered_charts:
        prefix = "oci://registry.suse.com/"
        if chart.chart.startswith(prefix):
            chart_path = chart.chart[len(prefix) :]
            if chart_path.startswith("edge/charts/"):
                oci_charts_by_path[chart_path] = chart.version
                ordered_oci_chart_paths.append(chart_path)

    release_images = []
    for image in parse_release_image_names(manifest_dir / "release_images.yaml"):
        image = substitute_placeholders(image, context)
        if not image.startswith("registry.suse.com/"):
            continue
        image = strip_registry(image)
        if image.startswith((f"edge/{release_family}/", "edge/", "suse/")):
            release_images.append(image)

    eib_image = tooling_manifest.get("eib", {}).get("image")
    if eib_image:
        release_images = upsert_image(
            release_images,
            strip_registry(substitute_placeholders(str(eib_image), context)),
            after_repo=f"edge/{release_family}/baremetal-operator",
        )

    release_images = upsert_image(release_images, f"edge/{release_family}/kubectl:{kubernetes_version}")
    release_images = upsert_image(release_images, f"edge/{release_family}/release-manifest:{release_version}")

    return ReleaseData(
        release_version=release_version,
        release_family=release_family,
        rke2_version=rke2_version,
        k3s_version=k3s_version,
        kubernetes_version=kubernetes_version,
        release_revision=f"release-{release_version}",
        charts_by_release_name=charts_by_release_name,
        oci_charts_by_path=oci_charts_by_path,
        ordered_oci_chart_paths=ordered_oci_chart_paths,
        edge_images=release_images,
    )


def image_repo(image: str) -> str:
    return image.rsplit(":", 1)[0]


def upsert_image(images: list[str], image: str, after_repo: str | None = None) -> list[str]:
    target_repo = image_repo(image)
    result = [existing for existing in images if image_repo(existing) != target_repo]
    if after_repo:
        for index, existing in enumerate(result):
            if image_repo(existing) == after_repo:
                result.insert(index + 1, image)
                return result
    result.append(image)
    return result


def replace_line(pattern: str, replacement: str, text: str) -> str:
    return re.sub(pattern, replacement, text, flags=re.MULTILINE)


def replace_upgrade_versions(text: str, release: ReleaseData, upgrade_type: str) -> str:
    if upgrade_type == "rke2":
        return replace_line(r"^(\s*version:\s*)v[^\s\"']+\+rke2r\d+(\s*)$", rf"\g<1>{release.rke2_version}\2", text)
    if upgrade_type == "k3s":
        return replace_line(r"^(\s*version:\s*)v[^\s\"']+\+k3s\d+(\s*)$", rf"\g<1>{release.k3s_version}\2", text)
    if upgrade_type == "os":
        text = replace_line(r'^(\s*version:\s*)["\']?[^"\'\s]+["\']?(\s*)$', rf'\g<1>"{release.release_version}"\2', text)
        return replace_kubectl_images(text, release)
    raise ValueError(f"unknown upgrade type: {upgrade_type}")


def replace_kubectl_images(text: str, release: ReleaseData) -> str:
    kubectl = f"registry.suse.com/edge/{release.release_family}/kubectl:{release.kubernetes_version}"
    return replace_line(
        r"^(\s*image:\s*)registry\.suse\.com/edge/\d+\.\d+/kubectl:[^\s\"']+(\s*)$",
        rf"\g<1>{kubectl}\2",
        text,
    )


def fleet_chart_version(chart: ChartInfo) -> str:
    if chart.release_name == "cert-manager" and not chart.version.startswith("v"):
        return f"v{chart.version}"
    return chart.version


def replace_fleet_chart_version(path: Path, text: str, release: ReleaseData) -> str:
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError:
        return text
    helm = data.get("helm") or {}
    release_name = str(helm.get("releaseName", "")).strip().strip('"').strip("'")
    chart = release.charts_by_release_name.get(release_name)
    if not chart:
        return text
    version = fleet_chart_version(chart)
    return replace_line(r'^(\s*version:\s*)["\']?[^"\'\s]+["\']?(\s*)$', rf'\g<1>"{version}"\2', text)


def replace_cert_manager_crds_url(text: str, release: ReleaseData) -> str:
    chart = release.charts_by_release_name.get("cert-manager")
    if not chart:
        return text
    version = fleet_chart_version(chart)
    return re.sub(
        r"https://github\.com/cert-manager/cert-manager/releases/download/[^/]+/cert-manager\.crds\.yaml",
        f"https://github.com/cert-manager/cert-manager/releases/download/{version}/cert-manager.crds.yaml",
        text,
    )


def generate_oci_artifacts(current_text: str, release: ReleaseData) -> str:
    lines = [line.strip() for line in current_text.splitlines() if line.strip()]
    updated: list[str] = []
    seen: set[str] = set()

    for line in lines:
        chart_path = line.rsplit(":", 1)[0]
        if chart_path in release.oci_charts_by_path:
            updated.append(f"{chart_path}:{release.oci_charts_by_path[chart_path]}")
            seen.add(chart_path)

    for chart_path in release.ordered_oci_chart_paths:
        if chart_path not in seen:
            updated.append(f"{chart_path}:{release.oci_charts_by_path[chart_path]}")
            seen.add(chart_path)

    return "\n".join(updated) + "\n"


def candidate_image_keys(image: str, release_family: str) -> list[str]:
    repo = image_repo(image)
    keys = [repo]

    edge_match = re.match(r"^edge/\d+\.\d+/(.+)$", repo)
    if edge_match:
        keys.append(f"edge/{release_family}/{edge_match.group(1)}")
        keys.append(f"edge/*/{edge_match.group(1)}")

    sles_match = re.match(r"^suse/sles/[^/]+/(.+)$", repo)
    if sles_match:
        keys.append(f"suse/sles/*/{sles_match.group(1)}")

    return keys


def generate_edge_images(current_text: str, release: ReleaseData) -> str:
    candidates: dict[str, str] = {}
    for image in release.edge_images:
        for key in candidate_image_keys(image, release.release_family):
            candidates[key] = image

    current_lines = [line.strip() for line in current_text.splitlines() if line.strip()]
    updated: list[str] = []
    seen_repos: set[str] = set()
    for line in current_lines:
        replacement = None
        for key in candidate_image_keys(line, release.release_family):
            if key in candidates:
                replacement = candidates[key]
                break
        image = replacement or line
        updated.append(image)
        seen_repos.add(image_repo(image))

    for image in (
        f"edge/{release.release_family}/edge-image-builder",
        f"edge/{release.release_family}/kubectl",
        f"edge/{release.release_family}/release-manifest",
    ):
        for candidate in release.edge_images:
            if image_repo(candidate) == image and image_repo(candidate) not in seen_repos:
                updated.append(candidate)
                seen_repos.add(image_repo(candidate))

    return "\n".join(updated) + "\n"


def generate_rke2_artifacts(release: ReleaseData) -> str:
    encoded_version = quote(release.rke2_version, safe="v0123456789.rke")
    return "".join(
        f"https://github.com/rancher/rke2/releases/download/{encoded_version}/{artifact}\n"
        for artifact in RKE2_ARTIFACTS
    )


def collect_updates(repo_root: Path, release: ReleaseData) -> dict[Path, str]:
    updates: dict[Path, str] = {}

    def update_file(relative: str, transform) -> None:
        path = repo_root / relative
        if not path.is_file():
            return
        old = path.read_text(encoding="utf-8")
        new = transform(old)
        if new != old:
            updates[path] = new

    for gitrepo in sorted((repo_root / "gitrepos/day2").glob("*-gitrepo.yaml")):
        old = gitrepo.read_text(encoding="utf-8")
        new = replace_line(r"^(\s*revision:\s*)\S+(\s*)$", rf"\g<1>{release.release_revision}\2", old)
        if new != old:
            updates[gitrepo] = new

    upgrade_roots = {
        "rke2": [
            "fleets/day2/system-upgrade-controller-plans/rke2-upgrade",
            "bundles/day2/system-upgrade-controller-plans/rke2-upgrade",
        ],
        "k3s": [
            "fleets/day2/system-upgrade-controller-plans/k3s-upgrade",
            "bundles/day2/system-upgrade-controller-plans/k3s-upgrade",
        ],
        "os": [
            "fleets/day2/system-upgrade-controller-plans/os-upgrade",
            "bundles/day2/system-upgrade-controller-plans/os-upgrade",
        ],
    }
    for upgrade_type, roots in upgrade_roots.items():
        for root in roots:
            for path in sorted((repo_root / root).glob("*.yaml")):
                old = path.read_text(encoding="utf-8")
                new = replace_upgrade_versions(old, release, upgrade_type)
                if new != old:
                    updates[path] = new

    for path in sorted((repo_root / "fleets/day2/chart-templates").glob("**/fleet.yaml")):
        old = path.read_text(encoding="utf-8")
        new = replace_fleet_chart_version(path, old, release)
        if new != old:
            updates[path] = new

    update_file(
        "fleets/day2/chart-templates/cert-manager/cert-manager-crds/kustomization.yaml",
        lambda text: replace_cert_manager_crds_url(text, release),
    )
    update_file("fleets/day2/eib-charts-upgrader/base/job.yaml", lambda text: replace_kubectl_images(text, release))
    update_file("scripts/day2/edge-release-images.txt", lambda text: generate_edge_images(text, release))
    update_file("scripts/day2/edge-release-helm-oci-artefacts.txt", lambda text: generate_oci_artifacts(text, release))
    update_file("scripts/day2/edge-release-rke2-images.txt", lambda _text: generate_rke2_artifacts(release))

    return updates


def print_summary(updates: dict[Path, str], repo_root: Path, show_diff: bool) -> None:
    if not updates:
        print("No Fleet examples updates needed.")
        return

    print(f"{len(updates)} file(s) would change:")
    for path in sorted(updates):
        print(f"  {path.relative_to(repo_root)}")

    if show_diff:
        for path in sorted(updates):
            old = path.read_text(encoding="utf-8").splitlines(keepends=True)
            new = updates[path].splitlines(keepends=True)
            diff = difflib.unified_diff(
                old,
                new,
                fromfile=str(path.relative_to(repo_root)),
                tofile=str(path.relative_to(repo_root)),
            )
            sys.stdout.writelines(diff)


def main() -> int:
    args = parse_args()
    repo_root = args.repo_root.resolve()
    if not (repo_root / "scripts/day2").is_dir():
        print(f"error: {repo_root} does not look like the fleet-examples repository root", file=sys.stderr)
        return 2

    temp_dir: tempfile.TemporaryDirectory[str] | None = None
    try:
        if args.container_image:
            temp_dir = extract_manifest_container(args.container_image, args.container_engine, args.skip_pull)
            manifest_dir = Path(temp_dir.name)
        elif args.manifest_url:
            temp_dir = extract_manifest_url(args.manifest_url)
            manifest_dir = Path(temp_dir.name)
        elif args.factory_ref:
            if not args.factory_repo:
                raise RuntimeError("--factory-repo is required with --factory-ref")
            temp_dir = extract_factory_ref(args.factory_repo.resolve(), args.factory_ref)
            manifest_dir = Path(temp_dir.name) / "release-manifest-image"
        else:
            manifest_dir = validate_manifest_dir(args.manifest_dir.resolve())

        release = build_release_data(manifest_dir)
        updates = collect_updates(repo_root, release)

        print(
            f"Generated Fleet examples updates for SUSE Edge {release.release_version} "
            f"(RKE2 {release.rke2_version}, K3s {release.k3s_version})."
        )
        print_summary(updates, repo_root, args.show_diff or (not args.write and not args.check))

        if args.check:
            return 1 if updates else 0
        if args.write:
            for path, content in updates.items():
                path.write_text(content, encoding="utf-8")
            if updates:
                print("Updated files written.")
        elif updates:
            print("Dry-run only. Re-run with --write to update files.")
        return 0
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        if temp_dir:
            temp_dir.cleanup()


if __name__ == "__main__":
    sys.exit(main())
