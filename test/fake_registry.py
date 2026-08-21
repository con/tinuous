"""
A minimal, in-process OCI registry serving a real (tiny) container image.

Enough of the `distribution spec`_ is implemented to pull an image: manifests
by tag or digest, and blobs by digest.  The images are built from
``/bin/busybox`` when it is available, so that the layouts tinuous produces can
be handed to ``podman`` and actually run.

.. _distribution spec:
   https://github.com/opencontainers/distribution-spec/blob/main/spec.md
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
import gzip
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import io
import json
from pathlib import Path
import shutil
import tarfile
import threading
from typing import Any, Optional

DOCKER_MANIFEST = "application/vnd.docker.distribution.manifest.v2+json"
DOCKER_INDEX = "application/vnd.docker.distribution.manifest.list.v2+json"
DOCKER_CONFIG = "application/vnd.docker.container.image.v1+json"
DOCKER_LAYER = "application/vnd.docker.image.rootfs.diff.tar.gzip"

OCI_MANIFEST = "application/vnd.oci.image.manifest.v1+json"
OCI_INDEX = "application/vnd.oci.image.index.v1+json"
OCI_CONFIG = "application/vnd.oci.image.config.v1+json"
OCI_LAYER = "application/vnd.oci.image.layer.v1.tar+gzip"

BUSYBOX = Path("/bin/busybox")


def digest_of(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def descriptor(media_type: str, data: bytes) -> dict[str, Any]:
    return {"mediaType": media_type, "digest": digest_of(data), "size": len(data)}


def canonical(obj: Any) -> bytes:
    """
    Serialize a manifest.  Deliberately *not* the most compact spelling: a
    consumer that reserializes rather than storing what it was served will end
    up with a different digest, and these tests should catch that.
    """
    return json.dumps(obj, indent=3).encode("utf-8")


def make_layer(message: str) -> tuple[bytes, str]:
    """
    Build a gzipped layer tarball holding a static busybox and a script that
    prints ``message``.  Returns the compressed bytes and the uncompressed
    digest (the "diff id" that goes into the image config).
    """
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as tf:
        for name in ("bin", "usr", "usr/bin"):
            info = tarfile.TarInfo(name)
            info.type = tarfile.DIRTYPE
            info.mode = 0o755
            tf.addfile(info)
        payload = BUSYBOX.read_bytes()
        info = tarfile.TarInfo("bin/busybox")
        info.size = len(payload)
        info.mode = 0o755
        tf.addfile(info, io.BytesIO(payload))
        for applet in ("bin/sh", "bin/echo"):
            link = tarfile.TarInfo(applet)
            link.type = tarfile.SYMTYPE
            link.linkname = "/bin/busybox"
            tf.addfile(link)
        script = f"#!/bin/sh\necho '{message}'\n".encode()
        info = tarfile.TarInfo("hello.sh")
        info.size = len(script)
        info.mode = 0o755
        tf.addfile(info, io.BytesIO(script))
    uncompressed = raw.getvalue()
    compressed = gzip.compress(uncompressed, mtime=0)
    return compressed, digest_of(uncompressed)


@dataclass
class Image:
    """An image built into a set of blobs, addressable by tag"""

    blobs: dict[str, bytes] = field(default_factory=dict)
    tags: dict[str, str] = field(default_factory=dict)
    media_types: dict[str, str] = field(default_factory=dict)

    def add(self, media_type: str, data: bytes) -> dict[str, Any]:
        desc = descriptor(media_type, data)
        self.blobs[desc["digest"]] = data
        self.media_types[desc["digest"]] = media_type
        return desc

    def add_manifest(
        self, message: str, architecture: str, *, oci: bool = False
    ) -> dict[str, Any]:
        layer, diff_id = make_layer(message)
        layer_desc = self.add(OCI_LAYER if oci else DOCKER_LAYER, layer)
        config = canonical(
            {
                "architecture": architecture,
                "os": "linux",
                "config": {"Cmd": ["/bin/sh", "/hello.sh"]},
                "rootfs": {"type": "layers", "diff_ids": [diff_id]},
            }
        )
        config_desc = self.add(OCI_CONFIG if oci else DOCKER_CONFIG, config)
        media_type = OCI_MANIFEST if oci else DOCKER_MANIFEST
        manifest = canonical(
            {
                "schemaVersion": 2,
                "mediaType": media_type,
                "config": config_desc,
                "layers": [layer_desc],
            }
        )
        desc = self.add(media_type, manifest)
        desc["platform"] = {"architecture": architecture, "os": "linux"}
        return desc

    def add_index(self, manifests: list[dict[str, Any]], *, oci: bool = False) -> str:
        media_type = OCI_INDEX if oci else DOCKER_INDEX
        index = canonical(
            {"schemaVersion": 2, "mediaType": media_type, "manifests": manifests}
        )
        return str(self.add(media_type, index)["digest"])

    def tag(self, name: str, digest: str) -> None:
        self.tags[name] = digest


def build_images(architecture: str = "amd64") -> dict[str, Image]:
    """
    Two images: a single-platform one (as `docker push` produces) and a
    multi-platform one (as `docker buildx --platform` produces).  Both use
    Docker media types, which is what GHCR serves in practice.
    """
    single = Image()
    single.tag("latest", single.add_manifest("Built at: single", architecture)["digest"])

    multi = Image()
    native = multi.add_manifest("Built at: multi", architecture)
    other = multi.add_manifest("Built at: multi", "s390x")
    index = multi.add_index([native, other])
    multi.tag("latest", index)
    multi.tag("v1", index)

    # GHCR package names can contain slashes -- dandi's containers are all
    # named "example-notebooks/<dandiset>" -- so exercise a nested name too.
    nested = Image()
    nested.tag(
        "latest", nested.add_manifest("Built at: nested", architecture)["digest"]
    )
    return {
        "testorg/single": single,
        "testorg/multi": multi,
        "testorg/notebooks/nested": nested,
    }


#: What a client must present, having exchanged its credentials at /token.
#: A registry bearer token is not the same thing as the password used to get
#: it -- presenting a GitHub token directly as a bearer is what GHCR rejects.
ISSUED_TOKEN = "issued-registry-token"

CREDENTIALS = ("tinuous", "s3cret")


class Handler(BaseHTTPRequestHandler):
    images: dict[str, Image]
    requests: list[str]
    require_auth: bool

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def _challenge(self, scope: str) -> None:
        realm = f"http://{self.headers.get('Host')}/token"
        body = b'{"errors":[{"code":"UNAUTHORIZED"}]}'
        self.send_response(401)
        self.send_header(
            "WWW-Authenticate",
            f'Bearer realm="{realm}",service="fake",scope="{scope}"',
        )
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _issue_token(self) -> None:
        header = self.headers.get("Authorization", "")
        if header.startswith("Basic "):
            decoded = base64.b64decode(header[len("Basic ") :]).decode()
            username, _, password = decoded.partition(":")
            if (username, password) == CREDENTIALS:
                body = json.dumps({"token": ISSUED_TOKEN}).encode()
                return self._send(200, body, "application/json")
        self._send(403, b'{"errors":[{"code":"DENIED"}]}', "application/json")

    def _authorized(self) -> bool:
        return self.headers.get("Authorization") == f"Bearer {ISSUED_TOKEN}"

    def _send(self, code: int, body: bytes, media_type: str, digest: str = "") -> None:
        self.send_response(code)
        self.send_header("Content-Type", media_type)
        self.send_header("Content-Length", str(len(body)))
        if digest:
            self.send_header("Docker-Content-Digest", digest)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        self.requests.append(self.path)
        parts = self.path.strip("/").split("/")
        if self.path.startswith("/token"):
            return self._issue_token()
        if self.path == "/v2/":
            return self._send(200, b"{}", "application/json")
        if self.require_auth and not self._authorized():
            name = "/".join(parts[1:-2]) if len(parts) >= 4 else ""
            return self._challenge(f"repository:{name}:pull")
        # /v2/<name>/<manifests|blobs>/<reference>, where <name> may itself
        # contain slashes (e.g. "dandi/example-notebooks/000409-ibl")
        if len(parts) < 4 or parts[0] != "v2":
            return self._send(404, b"{}", "application/json")
        name = "/".join(parts[1:-2])
        kind, reference = parts[-2], parts[-1]
        image = self.images.get(name)
        if image is None:
            return self._send(404, b'{"errors":[]}', "application/json")
        if kind == "manifests":
            digest = image.tags.get(reference, reference)
        elif kind == "blobs":
            digest = reference
        else:
            return self._send(404, b'{"errors":[]}', "application/json")
        data = image.blobs.get(digest)
        if data is None:
            return self._send(404, b'{"errors":[]}', "application/json")
        self._send(200, data, image.media_types[digest], digest)


class FakeRegistry:
    """A registry listening on localhost for the duration of a `with` block"""

    def __init__(
        self,
        images: Optional[dict[str, Image]] = None,
        require_auth: bool = False,
    ) -> None:
        self.images = build_images() if images is None else images
        self.requests: list[str] = []
        self.require_auth = require_auth

    def __enter__(self) -> FakeRegistry:
        handler = type(
            "BoundHandler",
            (Handler,),
            {
                "images": self.images,
                "requests": self.requests,
                "require_auth": self.require_auth,
            },
        )
        self.server = HTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    @property
    def hostname(self) -> str:
        host, port = self.server.server_address[:2]
        assert isinstance(host, str)
        return f"{host}:{port}"

    def image_ref(self, name: str, reference: str = "latest") -> str:
        sep = "@" if reference.startswith("sha256:") else ":"
        return f"{self.hostname}/{name}{sep}{reference}"


def have_busybox() -> bool:
    return BUSYBOX.is_file()


def have_podman() -> bool:
    return shutil.which("podman") is not None
