from __future__ import annotations

from collections.abc import Iterator
import json
from pathlib import Path
import shutil
import subprocess
from typing import Optional

from fake_registry import (
    FakeRegistry,
    build_images,
    have_busybox,
    have_podman,
    make_layer,
)
import pytest

from tinuous.ghcr import (
    DigestMismatch,
    OCILayout,
    digest_path,
    download_image,
    get_registry,
)


@pytest.fixture(scope="module")
def registry() -> Iterator[FakeRegistry]:
    with FakeRegistry() as reg:
        yield reg


@pytest.fixture(scope="module")
def runnable_registry() -> Iterator[FakeRegistry]:
    """A registry whose images have a shell in them, for the podman tests"""
    with FakeRegistry(build_images(runnable=True)) as reg:
        yield reg


def pull(reg: FakeRegistry, name: str, dest: Path, reference: str = "latest") -> str:
    client = get_registry(reg.hostname, insecure=True)
    return download_image(client, reg.image_ref(name, reference), dest, tag=reference)


def blobs(dest: Path) -> list[str]:
    return sorted(p.name for p in (dest / "blobs" / "sha256").iterdir())


@pytest.mark.parametrize(
    "name", ["testorg/single", "testorg/multi", "testorg/notebooks/nested"]
)
def test_layout_is_valid(registry: FakeRegistry, tmp_path: Path, name: str) -> None:
    dest = tmp_path / name.replace("/", "_")
    digest = pull(registry, name, dest)
    assert json.loads((dest / "oci-layout").read_text()) == {
        "imageLayoutVersion": "1.0.0"
    }
    index = json.loads((dest / "index.json").read_text())
    assert index["schemaVersion"] == 2
    (entry,) = index["manifests"]
    assert entry["digest"] == digest
    assert entry["annotations"] == {"org.opencontainers.image.ref.name": "latest"}
    assert entry["size"] == digest_path(dest, digest).stat().st_size
    assert entry["mediaType"] == json.loads(
        digest_path(dest, digest).read_bytes()
    )["mediaType"]


@pytest.mark.parametrize(
    "name", ["testorg/single", "testorg/multi", "testorg/notebooks/nested"]
)
def test_all_blobs_present_and_verified(
    registry: FakeRegistry, tmp_path: Path, name: str
) -> None:
    """Every blob is on disk, and every blob hashes to the name it is under."""
    import hashlib

    dest = tmp_path / name.replace("/", "_")
    pull(registry, name, dest)
    stored = set(blobs(dest))
    assert stored == {d.partition(":")[2] for d in registry.images[name].blobs}
    for encoded in stored:
        path = dest / "blobs" / "sha256" / encoded
        assert hashlib.sha256(path.read_bytes()).hexdigest() == encoded


def test_manifest_bytes_are_stored_verbatim(
    registry: FakeRegistry, tmp_path: Path
) -> None:
    """
    The manifest must be stored exactly as served.  Reserializing the JSON
    changes the digest, and an archive whose digests do not match the registry
    cannot be verified against it or restored under its original references.
    """
    dest = tmp_path / "testorg/single"
    digest = pull(registry, "testorg/single", dest)
    served = registry.images["testorg/single"].blobs[digest]
    assert digest_path(dest, digest).read_bytes() == served
    reserialized = json.dumps(
        json.loads(served), separators=(",", ":"), sort_keys=True
    ).encode()
    assert reserialized != served, "test image would not catch reserialization"


def test_multiarch_pulls_every_sub_manifest(
    registry: FakeRegistry, tmp_path: Path
) -> None:
    dest = tmp_path / "testorg/multi"
    digest = pull(registry, "testorg/multi", dest)
    index = json.loads(digest_path(dest, digest).read_bytes())
    assert len(index["manifests"]) == 2
    for entry in index["manifests"]:
        manifest = json.loads(digest_path(dest, entry["digest"]).read_bytes())
        for blob in [manifest["config"], *manifest["layers"]]:
            assert digest_path(dest, blob["digest"]).exists()


def test_pull_by_digest(registry: FakeRegistry, tmp_path: Path) -> None:
    digest = registry.images["testorg/single"].tags["latest"]
    dest = tmp_path / "bydigest"
    assert pull(registry, "testorg/single", dest, digest) == digest


def test_shared_blobs_downloaded_once(
    registry: FakeRegistry, tmp_path: Path
) -> None:
    """Both platforms of the multi-arch image share a layer; fetch it once."""
    dest = tmp_path / "testorg/multi"
    mark = len(registry.requests)
    pull(registry, "testorg/multi", dest)
    blob_requests = [p for p in registry.requests[mark:] if "/blobs/" in p]
    assert blob_requests
    assert len(blob_requests) == len(set(blob_requests))


def test_redownload_skipped_and_resumed(
    registry: FakeRegistry, tmp_path: Path
) -> None:
    dest = tmp_path / "resume"
    digest = pull(registry, "testorg/multi", dest)
    before = blobs(dest)

    # A complete layout is left alone.
    assert pull(registry, "testorg/multi", dest) == digest
    assert blobs(dest) == before

    # A layout missing a platform manifest has that whole subtree fetched
    # again, since a manifest blob is written only once its children are.
    index = json.loads(digest_path(dest, digest).read_bytes())
    digest_path(dest, index["manifests"][0]["digest"]).unlink()
    assert pull(registry, "testorg/multi", dest) == digest
    assert blobs(dest) == before


def test_corrupt_blob_is_not_left_behind(tmp_path: Path) -> None:
    """
    A blob whose content does not match its digest is rejected and removed, so
    that a later run does not mistake a truncated file for a complete one.
    """
    images = build_images()
    single = images["testorg/single"]
    manifest_digest = single.tags["latest"]
    manifest = json.loads(single.blobs[manifest_digest])
    layer_digest = manifest["layers"][0]["digest"]
    single.blobs[layer_digest] = single.blobs[layer_digest][:-10]

    dest = tmp_path / "corrupt"
    with FakeRegistry(images) as reg:
        with pytest.raises(DigestMismatch):
            pull(reg, "testorg/single", dest)
    assert not digest_path(dest, layer_digest).exists()
    assert list((dest / "blobs" / "sha256").glob("*.tmp")) == []
    # Without index.json the directory is not a layout, so an interrupted
    # download is never mistaken for a complete image.
    assert not OCILayout(dest).is_complete()


def test_oci_media_types(tmp_path: Path) -> None:
    """OCI-spelled media types work as well as the Docker ones GHCR serves."""
    from fake_registry import Image

    image = Image()
    image.tag(
        "latest", image.add_manifest("Built at: oci", "amd64", oci=True)["digest"]
    )
    dest = tmp_path / "oci"
    with FakeRegistry({"testorg/oci": image}) as reg:
        pull(reg, "testorg/oci", dest)
    index = json.loads((dest / "index.json").read_text())
    assert index["manifests"][0]["mediaType"] == (
        "application/vnd.oci.image.manifest.v1+json"
    )


def test_unsupported_media_type(tmp_path: Path) -> None:
    from fake_registry import Image, canonical

    image = Image()
    body = canonical({"schemaVersion": 1, "mediaType": "application/octet-stream"})
    image.tag("latest", image.add("application/octet-stream", body)["digest"])
    with FakeRegistry({"testorg/weird": image}) as reg:
        with pytest.raises(ValueError, match="Unsupported manifest media type"):
            pull(reg, "testorg/weird", tmp_path / "weird")


def test_index_digest_of_incomplete_layout(tmp_path: Path) -> None:
    assert OCILayout(tmp_path).index_digest() is None
    (tmp_path / "index.json").write_text("not json")
    assert OCILayout(tmp_path).index_digest() is None


@pytest.mark.parametrize("digest", ["sha256", "sha256:", ":abc", "sha256:../escape"])
def test_malformed_digest_rejected(tmp_path: Path, digest: str) -> None:
    with pytest.raises(ValueError):
        digest_path(tmp_path, digest)


@pytest.mark.skipif(not have_podman(), reason="podman is not installed")
@pytest.mark.skipif(not have_busybox(), reason="a static busybox is needed")
@pytest.mark.parametrize(
    "name,expected",
    [
        ("testorg/single", "single"),
        ("testorg/multi", "multi"),
        ("testorg/notebooks/nested", "nested"),
    ],
)
def test_podman_can_run_the_layout(
    runnable_registry: FakeRegistry, tmp_path: Path, name: str, expected: str
) -> None:
    """
    The point of the exercise: hand the layout to podman and get the image to
    run.  Also checks that the metadata file tinuous writes alongside does not
    upset it.
    """
    dest = tmp_path / name.replace("/", "_")
    pull(runnable_registry, name, dest)
    (dest / "package.json").write_text('{"package_name": "test"}\n')
    r = subprocess.run(
        [shutil.which("podman") or "podman", "run", "--rm", f"oci:{dest}"],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert r.returncode == 0, r.stderr
    assert f"Built at: {expected}" in r.stdout


def test_make_layer_is_reproducible() -> None:
    assert make_layer("x") == make_layer("x")
    assert make_layer("x") != make_layer("y")


def test_credentials_are_exchanged_for_a_registry_token(tmp_path: Path) -> None:
    """
    A registry answers 401 with a challenge, and the credentials have to be
    exchanged at the realm it names for a bearer token.  GHCR rejects a GitHub
    token presented directly as a bearer, so getting this wrong means no image
    is ever downloaded.
    """
    from fake_registry import CREDENTIALS, ISSUED_TOKEN

    dest = tmp_path / "authed"
    with FakeRegistry(require_auth=True) as reg:
        client = get_registry(reg.hostname, token=CREDENTIALS[1], insecure=True)
        download_image(client, reg.image_ref("testorg/single"), dest)
    assert OCILayout(dest).is_complete()
    assert any(p.startswith("/token") for p in reg.requests)
    assert client.auth.token == ISSUED_TOKEN


@pytest.fixture()
def no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    `oras` retries any failed request five times with an exponential backoff,
    including a permanent authentication failure, which takes it a bit over two
    minutes to give up on.  The tests are not interested in waiting.
    """
    import oras.decorator

    monkeypatch.setattr(oras.decorator.time, "sleep", lambda _seconds: None)


@pytest.mark.usefixtures("no_backoff")
@pytest.mark.parametrize("token", [None, "wrong"])
def test_unusable_credentials_are_rejected(
    tmp_path: Path, token: Optional[str]
) -> None:
    """
    Neither anonymous access nor a bad password gets an image out of a registry
    that requires authentication, and neither leaves a layout behind.
    """
    dest = tmp_path / "denied"
    with FakeRegistry(require_auth=True) as reg:
        client = get_registry(reg.hostname, token=token, insecure=True)
        with pytest.raises(ValueError, match="Cannot respond to request"):
            download_image(client, reg.image_ref("testorg/single"), dest)
    assert not OCILayout(dest).is_complete()
