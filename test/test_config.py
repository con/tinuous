import re
from typing import Any, Dict

import pytest

from tinuous.base import WorkflowSpec
from tinuous.config import GHPathsDict, GitHubConfig


@pytest.mark.parametrize(
    "data,cfg",
    [
        (
            {"paths": {"logs": "folder/subfolder/"}},
            GitHubConfig(
                paths=GHPathsDict(logs="folder/subfolder/"),
                workflows=WorkflowSpec(
                    regex=False, include=[re.compile(r".*")], exclude=[]
                ),
            ),
        ),
        (
            {"paths": {"logs": "folder/subfolder/"}, "workflows": []},
            GitHubConfig(
                paths=GHPathsDict(logs="folder/subfolder/"),
                workflows=WorkflowSpec(regex=False, include=[], exclude=[]),
            ),
        ),
        (
            {
                "paths": {"logs": "folder/subfolder/"},
                "workflows": ["build-macos.yaml"],
            },
            GitHubConfig(
                paths=GHPathsDict(logs="folder/subfolder/"),
                workflows=WorkflowSpec(
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
                workflows=WorkflowSpec(
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
                workflows=WorkflowSpec(
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
                workflows=WorkflowSpec(
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
                workflows=WorkflowSpec(
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
                workflows=WorkflowSpec(
                    regex=True,
                    include=[re.compile(r".*\.yaml")],
                    exclude=[re.compile(r"^build-macos\.yaml$")],
                ),
            ),
        ),
    ],
)
def test_parse_github_config(data: Dict[str, Any], cfg: GitHubConfig) -> None:
    assert GitHubConfig.parse_obj(data) == cfg
