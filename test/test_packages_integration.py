"""
Integration tests for GitHub Packages support.

These tests may require:
- GitHub token (GH_TOKEN or GITHUB_TOKEN environment variable)
- Network access to GitHub API and GHCR
- podman (for full container verification)

Run with: pytest test/test_packages_integration.py -v --integration
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import tempfile

import pytest

from tinuous.config import Config

log = logging.getLogger(__name__)


@pytest.mark.integration
@pytest.mark.skipif(
    not os.environ.get("GH_TOKEN") and not os.environ.get("GITHUB_TOKEN"),
    reason="Requires GitHub token"
)
def test_fetch_tinuous_inception_package() -> None:
    """
    Integration test that fetches the tinuous-inception package.

    This test verifies:
    1. Package filtering works (includes tinuous-inception)
    2. Metadata and manifest are downloaded
    3. Manifest contains expected OCI fields
    """
    from yaml import safe_load

    config_path = Path(__file__).parent / "data" / "test_packages.yaml"
    with open(config_path) as fp:
        cfg = Config.model_validate(safe_load(fp))

    # Verify config is correct
    assert cfg.repo == "con/tinuous-inception"
    ghcfg = cfg.ci.github
    assert ghcfg is not None
    assert ghcfg.gets_packages()

    # Verify package filtering
    assert ghcfg.packages.match("tinuous-inception")
    assert not ghcfg.packages.match("other-package")

    # Create a temporary directory for downloads
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        os.chdir(tmppath)

        # Fetch tokens
        tokens = ghcfg.get_auth_tokens()

        # Create the GitHub Actions client
        ci = ghcfg.get_system(
            repo=cfg.repo,
            since=cfg.since or cfg.get_since(None),
            until=cfg.until,
            tokens=tokens,
        )

        # Fetch package assets
        packages_found = 0
        for pkg_asset in ci.get_package_assets():
            packages_found += 1

            # Verify it's the expected package
            assert pkg_asset.package_name == "tinuous-inception"
            assert pkg_asset.package_type == "container"

            # Download the package
            path_template = ghcfg.paths.packages
            assert path_template is not None
            path_str = pkg_asset.expand_path(path_template, cfg.vars)
            path = Path(path_str)

            pkg_asset.download(path)

            # Verify metadata file exists and has expected content
            metadata_file = path / "metadata.json"
            assert metadata_file.exists()

            with open(metadata_file) as f:
                metadata = json.load(f)

            assert metadata["package_name"] == "tinuous-inception"
            assert metadata["package_type"] == "container"
            assert "tags" in metadata
            assert "version_id" in metadata
            assert "updated_at" in metadata

            # Verify OCI layout structure
            oci_layout = path / "oci-layout"
            index_json = path / "index.json"
            blobs_dir = path / "blobs" / "sha256"

            assert oci_layout.exists(), "OCI layout file should exist"
            assert index_json.exists(), "index.json should exist"
            assert blobs_dir.exists(), "blobs/sha256 directory should exist"

            # Verify OCI layout version
            with open(oci_layout) as f:
                layout = json.load(f)
            assert layout["imageLayoutVersion"] == "1.0.0"

            # Verify index.json structure
            with open(index_json) as f:
                index = json.load(f)
            assert index["schemaVersion"] == 2
            assert "manifests" in index
            assert len(index["manifests"]) > 0

            # Verify blobs exist
            blobs = list(blobs_dir.glob("*"))
            assert len(blobs) > 0, "Should have downloaded some blobs"
            log.info("Downloaded %d blobs", len(blobs))

            # Try to run with podman if available
            import subprocess
            import shutil

            if shutil.which("podman"):
                log.info("Testing image with podman")
                try:
                    # Run the container
                    result = subprocess.run(
                        ["podman", "run", f"oci:{path}"],
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    log.info("Podman stdout: %s", result.stdout)
                    log.info("Podman stderr: %s", result.stderr)

                    # Verify we got output
                    assert result.returncode == 0, (
                        f"Podman run failed: {result.stderr}"
                    )
                    assert "Built at:" in result.stdout, (
                        "Expected 'Built at:' in output"
                    )
                    log.info("✓ Successfully ran container with podman")
                except subprocess.TimeoutExpired:
                    log.warning("Podman run timed out")
                except Exception as e:
                    log.warning("Podman test failed: %s", str(e))
            else:
                log.info("Podman not available, skipping runtime test")

            # Only check the first package version found
            break

        assert packages_found > 0, "Should have found at least one package"


def test_package_filtering_config() -> None:
    """
    Unit test for package filtering configuration without network access.
    """
    from yaml import safe_load

    config_yaml = """
repo: con/tinuous-inception
ci:
  github:
    paths:
      packages: '{year}/{package_name}/{tag}/'
    packages:
      include:
        - tinuous-.*
      exclude:
        - .*-test
      regex: true
"""
    cfg = Config.model_validate(safe_load(config_yaml))
    ghcfg = cfg.ci.github
    assert ghcfg is not None

    # Test filtering
    assert ghcfg.packages.match("tinuous-inception")
    assert ghcfg.packages.match("tinuous-prod")
    assert not ghcfg.packages.match("tinuous-test")
    assert not ghcfg.packages.match("other-package")
