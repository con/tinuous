from abc import ABC, abstractmethod
from datetime import datetime
import re
from typing import Dict, Iterator, List, Optional, Pattern, Tuple

from pydantic import BaseModel, Field, validator

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
    def get_auth_token() -> str:
        ...  # pragma: no cover

    @abstractmethod
    def get_system(self, repo: str, since: datetime, token: str) -> CISystem:
        ...  # pragma: no cover


class GitHubConfig(CIConfig):
    artifacts_path: Optional[str] = None
    releases_path: Optional[str] = None
    workflows: Optional[List[str]] = None

    @staticmethod
    def get_auth_token() -> str:
        return GitHubActions.get_auth_token()

    def get_system(self, repo: str, since: datetime, token: str) -> GitHubActions:
        return GitHubActions(
            repo=repo,
            since=since,
            token=token,
            workflows=self.workflows,
        )


class TravisConfig(CIConfig):
    @staticmethod
    def get_auth_token() -> str:
        return Travis.get_auth_token()

    def get_system(self, repo: str, since: datetime, token: str) -> Travis:
        return Travis(
            repo=repo,
            since=since,
            token=token,
        )


class AppveyorConfig(CIConfig):
    accountName: str
    projectSlug: Optional[str] = None

    @staticmethod
    def get_auth_token() -> str:
        return Appveyor.get_auth_token()

    def get_system(self, repo: str, since: datetime, token: str) -> Appveyor:
        return Appveyor(
            repo=repo,
            since=since,
            token=token,
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
    types: List[EventType]
    secrets: Dict[str, Pattern] = Field(default_factory=dict)
    allow_secrets_regex: Optional[Pattern] = Field(None, alias="allow-secrets-regex")
    datalad: DataladConfig = Field(default_factory=DataladConfig)

    @validator("repo")
    def _validate_repo(cls, v: str) -> str:  # noqa: B902, U100
        if not re.fullmatch(r"[^/]+/[^/]+", v):
            raise ValueError("Repo must be in the form 'OWNER/NAME'")
        return v

    @validator("since")
    def _validate_since(cls, v: datetime) -> datetime:  # noqa: B902, U100
        if v.tzinfo is None:
            raise ValueError("'since' timestamp must include timezone offset")
        return v
