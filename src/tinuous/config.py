from abc import ABC, abstractmethod
from datetime import datetime
import re
from typing import Any, Dict, Iterator, List, Optional, Pattern, Tuple

from pydantic import Field, validator
from pydantic.fields import ModelField

from .appveyor import Appveyor
from .base import CISystem, EventType, NoExtraModel, WorkflowSpec
from .github import GitHubActions
from .travis import Travis


class PathsDict(NoExtraModel):
    logs: Optional[str] = None

    def gets_builds(self) -> bool:
        return self.logs is not None

    def gets_releases(self) -> bool:
        return False


class GHPathsDict(PathsDict):
    artifacts: Optional[str] = None
    releases: Optional[str] = None

    def gets_builds(self) -> bool:
        return self.logs is not None or self.artifacts is not None

    def gets_releases(self) -> bool:
        return self.releases is not None


class CIConfig(NoExtraModel, ABC):
    paths: PathsDict = Field(default_factory=PathsDict)

    @staticmethod
    @abstractmethod
    def get_auth_tokens() -> Dict[str, str]:
        ...  # pragma: no cover

    @abstractmethod
    def get_system(
        self,
        repo: str,
        since: datetime,
        until: Optional[datetime],
        tokens: Dict[str, str],
    ) -> CISystem:
        ...  # pragma: no cover

    def gets_builds(self) -> bool:
        return self.paths.gets_builds()

    def gets_releases(self) -> bool:
        return self.paths.gets_releases()


class GitHubConfig(CIConfig):
    paths: GHPathsDict = Field(default_factory=GHPathsDict)
    workflows: WorkflowSpec = Field(default_factory=WorkflowSpec)

    @validator("workflows", pre=True)
    def _workflow_list(cls, v: Any) -> Any:  # noqa: B902, U100
        if isinstance(v, list):
            return {"include": v}
        else:
            return v

    @staticmethod
    def get_auth_tokens() -> Dict[str, str]:
        return GitHubActions.get_auth_tokens()

    def get_system(
        self,
        repo: str,
        since: datetime,
        until: Optional[datetime],
        tokens: Dict[str, str],
    ) -> GitHubActions:
        return GitHubActions(
            repo=repo,
            since=since,
            until=until,
            token=tokens["github"],
            workflow_spec=self.workflows,
        )


class TravisConfig(CIConfig):
    @staticmethod
    def get_auth_tokens() -> Dict[str, str]:
        return Travis.get_auth_tokens()

    def get_system(
        self,
        repo: str,
        since: datetime,
        until: Optional[datetime],
        tokens: Dict[str, str],
    ) -> Travis:
        return Travis(
            repo=repo,
            since=since,
            until=until,
            token=tokens["travis"],
            gh_token=tokens["github"],
        )


class AppveyorConfig(CIConfig):
    accountName: str
    projectSlug: Optional[str] = None

    @staticmethod
    def get_auth_tokens() -> Dict[str, str]:
        return Appveyor.get_auth_tokens()

    def get_system(
        self,
        repo: str,
        since: datetime,
        until: Optional[datetime],
        tokens: Dict[str, str],
    ) -> Appveyor:
        return Appveyor(
            repo=repo,
            since=since,
            until=until,
            token=tokens["appveyor"],
            accountName=self.accountName,
            projectSlug=self.projectSlug,
        )


class CIConfigDict(NoExtraModel):
    github: Optional[GitHubConfig] = None
    travis: Optional[TravisConfig] = None
    appveyor: Optional[AppveyorConfig] = None

    def items(self) -> Iterator[Tuple[str, CIConfig]]:
        if self.github is not None:
            yield ("github", self.github)
        if self.travis is not None:
            yield ("travis", self.travis)
        if self.appveyor is not None:
            yield ("appveyor", self.appveyor)


class DataladConfig(NoExtraModel):
    enabled: bool = False
    cfg_proc: Optional[str] = None


class Config(NoExtraModel):
    repo: str
    vars: Dict[str, str] = Field(default_factory=dict)
    ci: CIConfigDict
    since: datetime
    until: Optional[datetime] = None
    types: List[EventType] = Field(default_factory=lambda: list(EventType))
    secrets: Dict[str, Pattern] = Field(default_factory=dict)
    allow_secrets_regex: Optional[Pattern] = Field(None, alias="allow-secrets-regex")
    datalad: DataladConfig = Field(default_factory=DataladConfig)

    @validator("repo")
    def _validate_repo(cls, v: str) -> str:  # noqa: B902, U100
        if not re.fullmatch(r"[^/]+/[^/]+", v):
            raise ValueError("Repo must be in the form 'OWNER/NAME'")
        return v

    @validator("since", "until")
    def _validate_datetimes(
        cls, v: Optional[datetime], field: ModelField  # noqa: B902, U100
    ) -> Optional[datetime]:
        if v is not None and v.tzinfo is None:
            raise ValueError(f"{field.name!r} timestamp must include timezone offset")
        return v
