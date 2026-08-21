"""
Downloading container images from a registry into an OCI image layout.

The heavy lifting -- parsing image references, the ``WWW-Authenticate`` token
dance, and credential handling -- is done by `oras-py`_, the Python SDK of the
`ORAS`_ project.  What is left, and what lives here, is walking a manifest and
writing the blobs out in the `OCI Image Layout`_ format that ``podman`` and
``skopeo`` consume.

.. _oras-py: https://github.com/oras-project/oras-py
.. _ORAS: https://oras.land
.. _OCI Image Layout:
   https://github.com/opencontainers/image-spec/blob/main/image-layout.md
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
from typing import Any, Optional

import oras.container
import oras.provider

from .util import log

#: Media types denoting a single image manifest.  GHCR serves the ``docker``
#: spelling for anything pushed by ``docker buildx``, which in practice is most
#: of what is in there, so both spellings have to be accepted.
MANIFEST_MEDIA_TYPES = frozenset(
    [
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    ]
)

#: Media types denoting an image index (a.k.a. a "manifest list"), used for
#: multi-platform images.
INDEX_MEDIA_TYPES = frozenset(
    [
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
    ]
)

ACCEPT_MANIFEST = ", ".join(sorted(MANIFEST_MEDIA_TYPES | INDEX_MEDIA_TYPES))

OCI_LAYOUT_VERSION = "1.0.0"

GHCR_HOSTNAME = "ghcr.io"

REF_NAME_ANNOTATION = "org.opencontainers.image.ref.name"

#: `oras` reads credentials for a registry out of `~/.docker/config.json` and
#: friends; tinuous passes the GitHub token in explicitly instead, so tell it
#: not to go looking.  Any username works for GHCR as long as the password is a
#: token with `read:packages`.
GHCR_USERNAME = "tinuous"


class DigestMismatch(Exception):
    """Raised when downloaded content does not hash to its expected digest"""

    def __init__(self, expected: str, actual: str) -> None:
        super().__init__(f"Expected content with digest {expected}, got {actual}")
        self.expected = expected
        self.actual = actual


def get_registry(
    hostname: str, token: Optional[str] = None, insecure: bool = False
) -> oras.provider.Registry:
    """
    Construct an `oras` registry client for ``hostname``, authenticating with
    ``token`` if given and anonymously otherwise.
    """
    registry = oras.provider.Registry(hostname=hostname, insecure=insecure)
    if token is not None:
        # `oras` exchanges these for a registry bearer token on the first 401,
        # which is the only form GHCR accepts; a GitHub token presented
        # directly as a bearer is rejected.
        registry.auth.set_basic_auth(GHCR_USERNAME, token)
    return registry


def digest_path(layout: Path, digest: str) -> Path:
    algorithm, _, encoded = digest.partition(":")
    if not algorithm or not encoded or "/" in encoded or encoded.startswith("."):
        raise ValueError(f"Malformed digest: {digest!r}")
    return layout / "blobs" / algorithm / encoded


@contextmanager
def staged_blob(layout: Path, digest: str) -> Iterator[Path]:
    """
    Context manager yielding a temporary path to write the blob for ``digest``
    to.  On a clean exit the content is checked against ``digest`` and moved
    into place; otherwise the partial file is removed.  Leaving a partial blob
    behind would be worse than not downloading it at all, as every later run
    would take it for a complete one.
    """
    target = digest_path(layout, digest)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    try:
        yield tmp
        actual = f"{digest.partition(':')[0]}:{hash_file(tmp, digest)}"
        if actual != digest:
            raise DigestMismatch(digest, actual)
        tmp.replace(target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def hash_file(path: Path, digest: str) -> str:
    h = hashlib.new(digest.partition(":")[0])
    with path.open("rb") as fp:
        while chunk := fp.read(65536):
            h.update(chunk)
    return h.hexdigest()


class OCILayout:
    """An OCI image layout directory being assembled on disk"""

    def __init__(self, path: Path) -> None:
        self.path = path

    def has_blob(self, digest: str) -> bool:
        return digest_path(self.path, digest).exists()

    def write_blob(self, digest: str, content: bytes) -> None:
        if self.has_blob(digest):
            return
        with staged_blob(self.path, digest) as tmp:
            tmp.write_bytes(content)

    def fetch_blob(
        self,
        registry: oras.provider.Registry,
        container: oras.container.Container,
        digest: str,
    ) -> None:
        if self.has_blob(digest):
            log.debug("Blob %s already present; not downloading", digest)
            return
        log.debug("Downloading blob %s", digest)
        with staged_blob(self.path, digest) as tmp:
            with registry.get_blob(container, digest, stream=True) as r:
                r.raise_for_status()
                with tmp.open("wb") as fp:
                    for chunk in r.iter_content(chunk_size=65536):
                        fp.write(chunk)

    def fetch_manifest(
        self,
        registry: oras.provider.Registry,
        container: oras.container.Container,
        reference: str,
    ) -> tuple[str, bytes]:
        """
        Fetch the manifest for ``reference`` (a tag or a digest), returning its
        digest and its bytes *as served*.  Reserializing the JSON would change
        the digest and thus produce an archive that cannot be checked against
        the registry it came from.
        """
        url = (
            f"{registry.prefix}://{container.registry}"
            f"/v2/{container.api_prefix}/manifests/{reference}"
        )
        r = registry.do_request(url, "GET", headers={"Accept": ACCEPT_MANIFEST})
        r.raise_for_status()
        content = r.content
        digest = r.headers.get("Docker-Content-Digest")
        if digest is None:
            return "sha256:" + hashlib.sha256(content).hexdigest(), content
        algorithm = digest.partition(":")[0]
        computed = f"{algorithm}:{hashlib.new(algorithm, content).hexdigest()}"
        if digest != computed:
            raise DigestMismatch(digest, computed)
        return digest, content

    def fetch_recursively(
        self,
        registry: oras.provider.Registry,
        container: oras.container.Container,
        reference: str,
    ) -> tuple[str, str]:
        """
        Fetch the manifest for ``reference`` and everything it references,
        storing it all as blobs.  Returns the manifest's digest and media type.
        """
        digest, content = self.fetch_manifest(registry, container, reference)
        manifest = json.loads(content)
        media_type = manifest.get("mediaType", "")
        if media_type in INDEX_MEDIA_TYPES:
            for entry in manifest.get("manifests", []):
                # A manifest blob is written only once everything below it is,
                # so one that is already present has a complete subtree.
                if not self.has_blob(entry["digest"]):
                    # Sub-manifests have to be fetched from the manifest
                    # endpoint; a registry will not serve them as blobs.
                    self.fetch_recursively(registry, container, entry["digest"])
        elif media_type in MANIFEST_MEDIA_TYPES:
            entries = list(manifest.get("layers", []))
            if "config" in manifest:
                entries.append(manifest["config"])
            for entry in entries:
                self.fetch_blob(registry, container, entry["digest"])
        else:
            raise ValueError(f"Unsupported manifest media type: {media_type!r}")
        self.write_blob(digest, content)
        return digest, media_type

    def write_index(
        self, digest: str, media_type: str, size: int, tag: Optional[str] = None
    ) -> None:
        """
        Write ``oci-layout`` and ``index.json``, which together make the
        directory a layout that ``podman`` and ``skopeo`` will read.  This is
        done last, so that an interrupted download leaves behind a directory
        that is not mistaken for a complete image.
        """
        entry: dict[str, Any] = {
            "mediaType": media_type,
            "digest": digest,
            "size": size,
        }
        if tag is not None:
            entry["annotations"] = {REF_NAME_ANNOTATION: tag}
        (self.path / "oci-layout").write_text(
            json.dumps({"imageLayoutVersion": OCI_LAYOUT_VERSION}) + "\n"
        )
        (self.path / "index.json").write_text(
            json.dumps({"schemaVersion": 2, "manifests": [entry]}, indent=2) + "\n"
        )

    def is_complete(self) -> bool:
        return (self.path / "index.json").exists()

    def index_digest(self) -> Optional[str]:
        """The digest recorded in ``index.json``, or `None` if there is none"""
        try:
            with (self.path / "index.json").open() as fp:
                index = json.load(fp)
            digest = index["manifests"][0]["digest"]
        except (OSError, ValueError, LookupError):
            return None
        assert isinstance(digest, str)
        return digest

    def iterfiles(self) -> Iterator[Path]:
        for p in sorted(self.path.rglob("*")):
            if p.is_file():
                yield p


def download_image(
    registry: oras.provider.Registry,
    image: str,
    path: Path,
    tag: Optional[str] = None,
) -> str:
    """
    Download ``image`` (e.g. ``ghcr.io/con/tinuous-inception:latest`` or
    ``...@sha256:...``) into an OCI image layout at ``path``, and return the
    digest of its manifest.
    """
    container = registry.get_container(image)
    reference = container.digest or container.tag
    path.mkdir(parents=True, exist_ok=True)
    layout = OCILayout(path)
    digest, media_type = layout.fetch_recursively(registry, container, reference)
    size = digest_path(path, digest).stat().st_size
    layout.write_index(digest, media_type, size, tag)
    return digest
