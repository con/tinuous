from abc import ABC, abstractmethod
from datetime import datetime, timezone
from enum import Enum
from functools import cached_property
import heapq
from pathlib import Path
from time import sleep
from typing import Any, Dict, Iterator, List, Optional, Tuple

from pydantic import BaseModel, Field
import requests

from .util import expand_template, log

COMMON_STATUS_MAP = {
    "success": "success",
    "passed": "success",
    "failure": "failed",
    "failed": "failed",
    "errored": "errored",
    "timed_out": "errored",
    "startup_failure": "errored",
    "neutral": "incomplete",
    "action_required": "incomplete",
    "cancelled": "incomplete",
    "skipped": "incomplete",
    "stale": "incomplete",
    # Error on unknown so we're forced to categorize them.
}

# Safeguard against typos:
assert set(COMMON_STATUS_MAP.values()) == {"success", "failed", "errored", "incomplete"}


class EventType(Enum):
    CRON = "cron"
    PUSH = "push"
    PULL_REQUEST = "pr"

    @classmethod
    def from_gh_event(cls, gh_event: str) -> Optional["EventType"]:
        return {
            "schedule": cls.CRON,
            "push": cls.PUSH,
            "pull_request": cls.PULL_REQUEST,
        }.get(gh_event)

    @classmethod
    def from_travis_event(cls, travis_event: str) -> Optional["EventType"]:
        return {
            "cron": cls.CRON,
            "push": cls.PUSH,
            "pull_request": cls.PULL_REQUEST,
        }.get(travis_event)


class APIClient:
    def __init__(self, base_url: str, headers: Dict[str, str]):
        self.base_url = base_url
        self.session = requests.Session()
        self.session.headers.update(headers)

    def get(self, path: str, **kwargs: Any) -> requests.Response:
        if path.lower().startswith(("http://", "https://")):
            url = path
        else:
            url = self.base_url.rstrip("/") + "/" + path.lstrip("/")
        i = 1
        r = self.session.get(url, **kwargs)
        while r.status_code >= 500 and i <= 10:
            log.debug(
                "Request to %s returned %d; waiting & retrying", url, r.status_code
            )
            sleep(i)
            i += 1
            r = self.session.get(url, **kwargs)
        r.raise_for_status()
        return r


class CISystem(ABC, BaseModel):
    repo: str
    token: str
    since: datetime
    fetched: List[Tuple[datetime, bool]] = Field(default_factory=list)

    @staticmethod
    @abstractmethod
    def get_auth_token() -> str:
        ...  # pragma: no cover

    @abstractmethod
    def get_build_assets(
        self, event_types: List[EventType], artifacts: bool = False
    ) -> Iterator["BuildAsset"]:
        ...  # pragma: no cover

    def register_build(self, ts: datetime, processed: bool) -> None:
        heapq.heappush(self.fetched, (ts, processed))

    def new_since(self) -> datetime:
        prev_ts = self.since
        while self.fetched:
            ts, processed = heapq.heappop(self.fetched)
            if not processed:
                break
            prev_ts = ts
        return prev_ts

    class Config:
        # <https://github.com/samuelcolvin/pydantic/issues/1241>
        arbitrary_types_allowed = True
        keep_untouched = (cached_property,)


class BuildAsset(ABC, BaseModel):
    client: APIClient
    created_at: datetime
    event_type: EventType
    event_id: str
    build_commit: str
    commit: Optional[str]
    number: int
    status: str

    class Config:
        # To allow APIClient:
        arbitrary_types_allowed = True

    def path_fields(self) -> Dict[str, str]:
        utc_date = self.created_at.astimezone(timezone.utc)
        commit = "UNK" if self.commit is None else self.commit
        return {
            "year": utc_date.strftime("%Y"),
            "month": utc_date.strftime("%m"),
            "day": utc_date.strftime("%d"),
            "hour": utc_date.strftime("%H"),
            "minute": utc_date.strftime("%M"),
            "second": utc_date.strftime("%S"),
            "type": self.event_type.value,
            "type_id": self.event_id,
            "build_commit": self.build_commit,
            "commit": commit,
            "number": str(self.number),
            "status": self.status,
            "common_status": COMMON_STATUS_MAP[self.status],
        }

    def expand_path(self, path_template: str, vars: Dict[str, str]) -> str:
        return expand_template(path_template, self.path_fields(), vars)

    @abstractmethod
    def download(self, path: Path) -> List[Path]:
        ...  # pragma: no cover


class BuildLog(BuildAsset):
    pass


class Artifact(BuildAsset):
    pass
