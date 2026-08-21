from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Optional

from fake_registry import FakeRegistry
import pytest
import requests
from yaml import safe_load

from tinuous.base import PackageSpec
from tinuous.config import Config
from tinuous.ghcr import get_registry
from tinuous.github import GHPackageAsset, GitHubActions, Package, PackageVersion

ORG = "https://api.github.com/orgs/dandi"

PACKAGES = [
    {
        "id": 1,
        "name": "example-notebooks/000409-ibl",
        "package_type": "container",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-08-20T00:00:00Z",
        "url": f"{ORG}/packages/container/example-notebooks%2F000409-ibl",
        "repository": {"full_name": "dandi/example-notebooks"},
    },
    {
        "id": 2,
        "name": "dandi-api",
        "package_type": "container",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-08-20T00:00:00Z",
        "url": f"{ORG}/packages/container/dandi-api",
        "repository": {"full_name": "dandi/dandi-archive"},
    },
    {
        "id": 3,
        "name": "orphan",
        "package_type": "container",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-08-20T00:00:00Z",
        "url": f"{ORG}/packages/container/orphan",
    },
]


def version(
    version_id: int, tags: list[str], updated: str = "2026-08-20T00:00:00Z"
) -> dict[str, Any]:
    return {
        "id": version_id,
        "name": f"sha256:{version_id:064x}",
        "url": f"{ORG}/packages/container/dandi-api/versions/{version_id}",
        "html_url": (
            f"https://github.com/dandi/dandi-archive/pkgs/container/x/{version_id}"
        ),
        "created_at": updated,
        "updated_at": updated,
        "metadata": {"package_type": "container", "container": {"tags": tags}},
    }


VERSIONS = [
    version(10, ["latest", "v2"]),
    version(11, [], "2026-08-19T00:00:00Z"),
    version(12, ["v1"], "2026-01-05T00:00:00Z"),
]


def make_ci(
    monkeypatch: pytest.MonkeyPatch,
    package_spec: Optional[PackageSpec] = None,
    since: str = "2020-01-01T00:00:00Z",
    org_status: int = 200,
) -> GitHubActions:
    from tinuous.base import GHWorkflowSpec

    ci = GitHubActions(
        repo="dandi/dandi-archive",
        token="not-a-real-token",
        since=datetime.fromisoformat(since.replace("Z", "+00:00")),
        workflow_spec=GHWorkflowSpec(),
        package_spec=package_spec or PackageSpec(),
    )

    def paginate(
        self: GitHubActions, path: str, params: Optional[dict] = None
    ) -> Any:
        if path.endswith("/packages"):
            assert params == {"package_type": "container"}
            if path.startswith("/orgs/") and org_status != 200:
                raise http_error(org_status, path)
            if path.startswith("/users/"):
                raise http_error(404, path)
            return iter(PACKAGES)
        assert "/versions" in path
        assert params is None
        return iter(VERSIONS)

    monkeypatch.setattr(GitHubActions, "paginate", paginate)
    return ci


def http_error(status: int, url: str) -> requests.HTTPError:
    response = requests.Response()
    response.status_code = status
    response.url = url
    return requests.HTTPError(f"{status} for {url}", response=response)


def test_package_owner_and_repo() -> None:
    pkg = Package.model_validate(PACKAGES[0])
    assert pkg.owner_endpoint == "/orgs/dandi"
    assert pkg.owner == "dandi"
    assert pkg.repo_name == "dandi/example-notebooks"
    assert Package.model_validate(PACKAGES[2]).repo_name is None


def test_package_version_tags() -> None:
    assert PackageVersion.model_validate(VERSIONS[0]).tags == ["latest", "v2"]
    assert PackageVersion.model_validate(VERSIONS[1]).tags == []
    stripped = dict(VERSIONS[0], metadata=None)
    assert PackageVersion.model_validate(stripped).tags == []


def test_packages_scoped_to_repo(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    The API only lists packages per owner.  dandi's containers all belong to
    dandi/example-notebooks, so a backup of dandi/dandi-archive must not drag
    them in.
    """
    ci = make_ci(monkeypatch)
    assert [p.name for p in ci.get_packages()] == ["dandi-api"]


def test_packages_owner_wide(monkeypatch: pytest.MonkeyPatch) -> None:
    ci = make_ci(monkeypatch, PackageSpec(owner_wide=True))
    assert [p.name for p in ci.get_packages()] == [
        "example-notebooks/000409-ibl",
        "dandi-api",
        "orphan",
    ]


def test_packages_falls_back_to_user_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ci = make_ci(monkeypatch, org_status=404)
    assert list(ci.get_packages()) == []


def test_packages_missing_scope_is_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ci = make_ci(monkeypatch, org_status=403)
    with pytest.raises(RuntimeError, match="read:packages"):
        list(ci.get_packages())


def test_package_assets_skip_untagged(monkeypatch: pytest.MonkeyPatch) -> None:
    ci = make_ci(monkeypatch)
    assert [a.version_id for a in ci.get_package_assets()] == [10, 12]

    ci = make_ci(monkeypatch, PackageSpec(untagged=True))
    assert [a.version_id for a in ci.get_package_assets()] == [10, 11, 12]


def test_package_assets_respect_since_and_until(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ci = make_ci(monkeypatch, since="2026-06-01T00:00:00Z")
    assert [a.version_id for a in ci.get_package_assets()] == [10]

    ci = make_ci(monkeypatch)
    ci.until = datetime(2026, 6, 1, tzinfo=timezone.utc)
    assert [a.version_id for a in ci.get_package_assets()] == [12]


def test_package_assets_respect_include(monkeypatch: pytest.MonkeyPatch) -> None:
    ci = make_ci(
        monkeypatch, PackageSpec.model_validate({"include": ["nope"], "regex": False})
    )
    assert list(ci.get_package_assets()) == []


def make_asset(**kwargs: Any) -> GHPackageAsset:
    fields: dict[str, Any] = {
        "registry": get_registry("ghcr.io"),
        "owner": "dandi",
        "package_name": "example-notebooks/000409-ibl",
        "package_type": "container",
        "version_id": 10,
        "digest": "sha256:" + "ab" * 32,
        "tags": ["latest", "v2"],
        "updated_at": datetime(2026, 8, 20, 12, 30, tzinfo=timezone.utc),
        "html_url": None,
    }
    fields.update(kwargs)
    return GHPackageAsset(**fields)


def test_asset_image_reference() -> None:
    asset = make_asset()
    assert asset.image == (
        "ghcr.io/dandi/example-notebooks/000409-ibl@sha256:" + "ab" * 32
    )


def test_asset_path_expansion() -> None:
    asset = make_asset()
    path = asset.expand_path("{year}/{month}/{ci}/{package_name}/{digest}/", {})
    # Slashes and colons are escaped so that a package name or digest cannot
    # introduce extra path components.
    assert path == (
        "2026/08/github/example-notebooks%2f000409-ibl/sha256%3a" + "ab" * 32 + "/"
    )
    assert asset.expand_path("{tag}/{tags}/{version_id}/{type}", {}) == (
        "latest/latest,v2/10/package"
    )


def test_asset_path_untagged_falls_back_to_digest() -> None:
    asset = make_asset(tags=[])
    assert asset.expand_path("{tag}", {}) == "sha256%3a" + "ab" * 32


def test_asset_download_and_reuse(tmp_path: Path) -> None:
    with FakeRegistry() as reg:
        client = get_registry(reg.hostname, insecure=True)
        digest = reg.images["testorg/notebooks/nested"].tags["latest"]
        asset = make_asset(
            registry=client,
            owner="testorg",
            package_name="notebooks/nested",
            digest=digest,
        )
        assert asset.image == f"{reg.hostname}/testorg/notebooks/nested@{digest}"

        dest = tmp_path / "pkg"
        paths = asset.download(dest)
        assert paths
        metadata = json.loads((dest / "package.json").read_text())
        assert metadata["digest"] == digest
        assert metadata["tags"] == ["latest", "v2"]
        assert metadata["package_name"] == "notebooks/nested"
        assert metadata["image"] == asset.image

        # A second run over a complete layout downloads nothing.
        assert asset.download(dest) == []

        # A version whose digest differs replaces the layout in place, which is
        # what happens when a path template keyed on `{tag}` sees a moved tag.
        other = reg.images["testorg/multi"].tags["latest"]
        asset2 = make_asset(
            registry=client, owner="testorg", package_name="multi", digest=other
        )
        dest2 = tmp_path / "moved"
        asset.download(dest2)
        assert asset2.download(dest2)
        index = json.loads((dest2 / "index.json").read_text())
        assert index["manifests"][0]["digest"] == other


def test_config_accepts_package_options() -> None:
    cfg = Config.model_validate(
        safe_load(
            """
repo: dandi/example-notebooks
ci:
  github:
    paths:
      packages: '{year}/{month}/{ci}/packages/{package_name}/{digest}/'
    packages:
      include:
        - '0004.*'
      exclude:
        - '.*-test'
      regex: true
      owner_wide: true
      untagged: true
"""
        )
    )
    ghcfg = cfg.ci.github
    assert ghcfg is not None
    assert ghcfg.gets_packages()
    assert ghcfg.packages.owner_wide
    assert ghcfg.packages.untagged
    assert ghcfg.packages.match("000409-ibl")
    assert not ghcfg.packages.match("000409-test")
    assert not ghcfg.packages.match("dandi-api")


def test_config_package_list_shorthand() -> None:
    cfg = Config.model_validate(
        safe_load(
            """
repo: con/tinuous
ci:
  github:
    paths:
      packages: '{package_name}/{digest}/'
    packages:
      - tinuous-inception
"""
        )
    )
    ghcfg = cfg.ci.github
    assert ghcfg is not None
    assert ghcfg.packages.match("tinuous-inception")
    assert not ghcfg.packages.match("something-else")
    assert not ghcfg.packages.owner_wide
    assert not ghcfg.packages.untagged


def test_config_without_packages_path() -> None:
    cfg = Config.model_validate(
        safe_load("repo: con/tinuous\nci:\n  github:\n    paths:\n      logs: 'l/'\n")
    )
    ghcfg = cfg.ci.github
    assert ghcfg is not None
    assert not ghcfg.gets_packages()
    assert ghcfg.packages.match("anything")
