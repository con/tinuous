from abc import ABC, abstractmethod
from datetime import datetime
import re
from typing import Dict, Iterator, List, Optional, Pattern, Tuple

from pydantic import BaseModel, Field, validator
from pydantic.fields import ModelField

from .appveyor import Appveyor
from .base import CISystem, EventType
from .github import GitHubActions
from .travis import Travis


class NoExtraModel(BaseModel):
    class Config:
        allow_population_by_field_name = True
        extra = "forbid"


class CIConfig(NoExtraModel, ABC):
    path: str

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


class GitHubConfig(CIConfig):
    artifacts_path: Optional[str] = None
    releases_path: Optional[str] = None
    workflows: Optional[List[str]] = None

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
            workflows=self.workflows,
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
    types: List[EventType]
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
