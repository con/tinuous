from __future__ import annotations

import re
from typing import Any

import pytest

from tinuous.base import GHWorkflowSpec
from tinuous.config import GHPathsDict, GitHubConfig


@pytest.mark.parametrize(
    "data,cfg",
    [
        (
            {"paths": {"logs": "folder/subfolder/"}},
            GitHubConfig(
                paths=GHPathsDict(logs="folder/subfolder/"),
                workflows=GHWorkflowSpec(
                    regex=False, include=[re.compile(r".*")], exclude=[]
                ),
            ),
        ),
        (
            {"paths": {"logs": "folder/subfolder/"}, "workflows": []},
            GitHubConfig(
                paths=GHPathsDict(logs="folder/subfolder/"),
                workflows=GHWorkflowSpec(regex=False, include=[], exclude=[]),
            ),
        ),
        (
            {
                "paths": {"logs": "folder/subfolder/"},
                "workflows": ["build-macos.yaml"],
            },
            GitHubConfig(
                paths=GHPathsDict(logs="folder/subfolder/"),
                workflows=GHWorkflowSpec(
                    regex=False,
                    include=[re.compile(r"\Abuild\-macos\.yaml\Z")],
                    exclude=[],
                ),
            ),
        ),
        (
            {
                "paths": {"logs": "folder/subfolder/"},
                "workflows": ["build-macos.yaml", "build-windows.yaml"],
            },
            GitHubConfig(
                paths=GHPathsDict(logs="folder/subfolder/"),
                workflows=GHWorkflowSpec(
                    regex=False,
                    include=[
                        re.compile(r"\Abuild\-macos\.yaml\Z"),
                        re.compile(r"\Abuild\-windows\.yaml\Z"),
                    ],
                    exclude=[],
                ),
            ),
        ),
        (
            {
                "paths": {"logs": "folder/subfolder/"},
                "workflows": {"include": ["build-macos.yaml", "build-windows.yaml"]},
            },
            GitHubConfig(
                paths=GHPathsDict(logs="folder/subfolder/"),
                workflows=GHWorkflowSpec(
                    regex=False,
                    include=[
                        re.compile(r"\Abuild\-macos\.yaml\Z"),
                        re.compile(r"\Abuild\-windows\.yaml\Z"),
                    ],
                    exclude=[],
                ),
            ),
        ),
        (
            {
                "paths": {"logs": "folder/subfolder/"},
                "workflows": {
                    "include": ["build-macos.yaml", "build-windows.yaml"],
                    "exclude": ["*.yml"],
                },
            },
            GitHubConfig(
                paths=GHPathsDict(logs="folder/subfolder/"),
                workflows=GHWorkflowSpec(
                    regex=False,
                    include=[
                        re.compile(r"\Abuild\-macos\.yaml\Z"),
                        re.compile(r"\Abuild\-windows\.yaml\Z"),
                    ],
                    exclude=[re.compile(r"\A\*\.yml\Z")],
                ),
            ),
        ),
        (
            {
                "paths": {"logs": "folder/subfolder/"},
                "workflows": {
                    "include": ["build-macos.yaml", "build-windows.yaml"],
                    "exclude": [r".*\.yml"],
                    "regex": True,
                },
            },
            GitHubConfig(
                paths=GHPathsDict(logs="folder/subfolder/"),
                workflows=GHWorkflowSpec(
                    regex=True,
                    include=[
                        re.compile(r"build-macos.yaml"),
                        re.compile(r"build-windows.yaml"),
                    ],
                    exclude=[re.compile(r".*\.yml")],
                ),
            ),
        ),
        (
            {
                "paths": {"logs": "folder/subfolder/"},
                "workflows": {
                    "include": [r".*\.yaml"],
                    "exclude": [r"^build-macos\.yaml$"],
                    "regex": True,
                },
            },
            GitHubConfig(
                paths=GHPathsDict(logs="folder/subfolder/"),
                workflows=GHWorkflowSpec(
                    regex=True,
                    include=[re.compile(r".*\.yaml")],
                    exclude=[re.compile(r"^build-macos\.yaml$")],
                ),
            ),
        ),
        (
            {
                "paths": {
                    "packages": "{year}/{package_name}/{tag}/",
                },
            },
            GitHubConfig(
                paths=GHPathsDict(packages="{year}/{package_name}/{tag}/"),
                workflows=GHWorkflowSpec(
                    regex=False, include=[re.compile(r".*")], exclude=[]
                ),
            ),
        ),
    ],
)
def test_parse_github_config(data: dict[str, Any], cfg: GitHubConfig) -> None:
    assert GitHubConfig.model_validate(data) == cfg


def test_ghpathsdict_gets_packages() -> None:
    """Test gets_packages method for GHPathsDict."""
    paths_without_packages = GHPathsDict(logs="logs/")
    assert not paths_without_packages.gets_packages()

    paths_with_packages = GHPathsDict(packages="{year}/{package_name}/")
    assert paths_with_packages.gets_packages()


def test_package_filtering() -> None:
    """Test package filtering with include/exclude."""
    # Test with list of packages (converted to include)
    data = {
        "paths": {"packages": "{year}/{package_name}/"},
        "packages": ["tinuous-inception", "nwb2bids"],
    }
    cfg = GitHubConfig.model_validate(data)
    assert cfg.packages.match("tinuous-inception")
    assert cfg.packages.match("nwb2bids")
    assert not cfg.packages.match("other-package")

    # Test with explicit include/exclude
    data = {
        "paths": {"packages": "{year}/{package_name}/"},
        "packages": {
            "include": ["tinuous-.*"],
            "exclude": [".*-test"],
            "regex": True,
        },
    }
    cfg = GitHubConfig.model_validate(data)
    assert cfg.packages.match("tinuous-inception")
    assert cfg.packages.match("tinuous-prod")
    assert not cfg.packages.match("tinuous-test")
    assert not cfg.packages.match("other-package")

    # Test default (include all)
    data = {"paths": {"packages": "{year}/{package_name}/"}}
    cfg = GitHubConfig.model_validate(data)
    assert cfg.packages.match("any-package")
    assert cfg.packages.match("tinuous-inception")
