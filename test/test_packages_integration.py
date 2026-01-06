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
import os
from pathlib import Path
import tempfile

import pytest

from tinuous.config import Config


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
            
            files = pkg_asset.download(path)
            
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
            
            # Verify manifest file exists for containers
            manifest_file = path / "manifest.json"
            if manifest_file.exists():
                with open(manifest_file) as f:
                    manifest = json.load(f)
                
                # Verify OCI/Docker manifest structure
                assert "config" in manifest or "manifests" in manifest
                if "config" in manifest:
                    # Single-platform manifest
                    assert "layers" in manifest
                    assert "schemaVersion" in manifest
                elif "manifests" in manifest:
                    # Multi-platform manifest list
                    assert "schemaVersion" in manifest
                    assert isinstance(manifest["manifests"], list)
            
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
