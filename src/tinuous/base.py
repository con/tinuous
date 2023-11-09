from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from datetime import datetime, timezone
from enum import Enum
import heapq
import os
from pathlib import Path, PurePosixPath
import platform
import re
from shutil import rmtree
import sys
import tempfile
from time import sleep
from typing import Any, List, Optional, Tuple
from zipfile import BadZipFile, ZipFile

from pydantic import BaseModel, BeforeValidator, Field, ValidationInfo
import requests
from requests.exceptions import ChunkedEncodingError
from requests.exceptions import ConnectionError as ReqConError

from . import __url__, __version__
from .util import (
    delay_until,
    expand_template,
    log,
    parse_retry_after,
    sanitize_pathname,
)

if sys.version_info >= (3, 9):
    from typing import Annotated
else:
    from typing_extensions import Annotated

USER_AGENT = "tinuous/{} ({}) requests/{} {}/{}".format(
    __version__,
    __url__,
    requests.__version__,
    platform.python_implementation(),
    platform.python_version(),
)


class CommonStatus(Enum):
    SUCCESS = "success"
    FAILED = "failed"
    ERRORED = "errored"
    INCOMPLETE = "incomplete"


COMMON_STATUS_MAP = {
    "success": CommonStatus.SUCCESS,
    "passed": CommonStatus.SUCCESS,
    "failure": CommonStatus.FAILED,
    "failed": CommonStatus.FAILED,
    "errored": CommonStatus.ERRORED,
    "timed_out": CommonStatus.ERRORED,
    "startup_failure": CommonStatus.ERRORED,
    "neutral": CommonStatus.INCOMPLETE,
    "action_required": CommonStatus.INCOMPLETE,
    "cancelled": CommonStatus.INCOMPLETE,
    "canceled": CommonStatus.INCOMPLETE,
    "skipped": CommonStatus.INCOMPLETE,
    "stale": CommonStatus.INCOMPLETE,
    "started": CommonStatus.INCOMPLETE,
    # Statuses specific to CircleCI:
    "retried": CommonStatus.INCOMPLETE,
    "infrastructure_fail": CommonStatus.ERRORED,
    "timedout": CommonStatus.ERRORED,
    "not_run": CommonStatus.INCOMPLETE,
    "running": CommonStatus.INCOMPLETE,
    "queued": CommonStatus.INCOMPLETE,
    "not_running": CommonStatus.INCOMPLETE,
    "no_tests": CommonStatus.SUCCESS,
    "fixed": CommonStatus.SUCCESS,
    # Error on unknown so we're forced to categorize them.
}


class EventType(Enum):
    CRON = "cron"
    PUSH = "push"
    PULL_REQUEST = "pr"
    MANUAL = "manual"

    @classmethod
    def from_gh_event(cls, gh_event: str) -> Optional["EventType"]:
        return {
            "schedule": cls.CRON,
            "push": cls.PUSH,
            "pull_request": cls.PULL_REQUEST,
            "pull_request_target": cls.PULL_REQUEST,
            "workflow_dispatch": cls.MANUAL,
            "repository_dispatch": cls.MANUAL,
        }.get(gh_event)

    @classmethod
    def from_travis_event(cls, travis_event: str) -> Optional["EventType"]:
        return {
            "cron": cls.CRON,
            "push": cls.PUSH,
            "pull_request": cls.PULL_REQUEST,
            "api": cls.MANUAL,
        }.get(travis_event)


class APIClient:
    MAX_RETRIES = 12
    ZIPFILE_RETRIES = 5

    def __init__(self, base_url: str, headers: dict[str, str], is_github: bool = False):
        self.base_url = base_url
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self.session.headers.update(headers)
        self.is_github = is_github

    def get(self, path: str, **kwargs: Any) -> requests.Response:
        if path.lower().startswith(("http://", "https://")):
            url = path
        else:
            url = self.base_url.rstrip("/") + "/" + path.lstrip("/")
        i = 0
        while True:
            r = self.session.get(url, **kwargs)
            if (
                r.status_code == 429
                and "Retry-After" in r.headers
                and (delay := parse_retry_after(r.headers["Retry-After"])) is not None
            ):
                # Add 1 because `sleep()` isn't always exactly accurate
                delay += 1
                log.warning("Rate limit exceeded; sleeping for %s seconds", delay)
                sleep(delay)
            elif (
                r.status_code >= 500 or r.status_code == 429
            ) and i < self.MAX_RETRIES:
                log.warning(
                    "Request to %s returned %d; waiting & retrying", url, r.status_code
                )
                sleep(1.25 * 2**i)
                i += 1
            elif (
                self.is_github
                and r.status_code == 403
                and "API rate limit exceeded" in r.json().get("message", "")
            ):
                delay = delay_until(
                    datetime.fromtimestamp(
                        int(r.headers["x-ratelimit-reset"]), tz=timezone.utc
                    )
                )
                log.warning("Rate limit exceeded; sleeping for %s seconds", delay)
                sleep(delay)
            else:
                r.raise_for_status()
                return r

    def download(
        self, path: str, filepath: Path, headers: dict[str, str] | None = None
    ) -> None:
        i = 0
        while True:
            try:
                try:
                    r = self.get(path, stream=True, headers=headers)
                    with filepath.open("wb") as fp:
                        for chunk in r.iter_content(chunk_size=8192):
                            fp.write(chunk)
                except (ChunkedEncodingError, ReqConError) as e:
                    if i < self.MAX_RETRIES:
                        log.warning(
                            "Download of %s interrupted: %s; waiting & retrying",
                            path,
                            str(e),
                        )
                        i += 1
                        sleep(i)
                    else:
                        log.error("Max retries exceeded")
                        raise
                else:
                    break
            except BaseException:
                filepath.unlink(missing_ok=True)
                raise

    def download_zipfile(self, path: str, target_dir: Path) -> None:
        fd, fpath = tempfile.mkstemp()
        os.close(fd)
        zippath = Path(fpath)
        i = 0
        while True:
            self.download(path, zippath, headers={"Accept": "application/zip"})
            try:
                with ZipFile(zippath) as zf:
                    zf.extractall(target_dir)
            except BadZipFile:
                rmtree(target_dir)
                if i < self.ZIPFILE_RETRIES:
                    log.error("Invalid zip file retrieved; waiting and retrying")
                    i += 1
                    sleep(i * i)
                else:
                    raise
            except BaseException:
                rmtree(target_dir)
                raise
            else:
                break
            finally:
                zippath.unlink(missing_ok=True)


class CISystem(ABC, BaseModel):
    repo: str
    token: str
    since: datetime
    until: Optional[datetime] = None
    fetched: List[Tuple[datetime, bool]] = Field(default_factory=list)

    @staticmethod
    @abstractmethod
    def get_auth_tokens() -> dict[str, str]:
        ...

    @abstractmethod
    def get_build_assets(
        self, event_types: list[EventType], logs: bool, artifacts: bool
    ) -> Iterator[BuildAsset]:
        ...

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


# The `arbitrary_types_allowed` is for APIClient
class BuildAsset(ABC, BaseModel, arbitrary_types_allowed=True):
    client: APIClient
    created_at: datetime
    event_type: EventType
    event_id: str
    build_commit: str
    commit: Optional[str] = None
    number: int
    status: str

    def path_fields(self) -> dict[str, Any]:
        utc_date = self.created_at.astimezone(timezone.utc)
        commit = "UNK" if self.commit is None else self.commit
        return {
            "timestamp": utc_date,
            "timestamp_local": self.created_at.astimezone(),
            "year": utc_date.strftime("%Y"),
            "month": utc_date.strftime("%m"),
            "day": utc_date.strftime("%d"),
            "hour": utc_date.strftime("%H"),
            "minute": utc_date.strftime("%M"),
            "second": utc_date.strftime("%S"),
            "type": self.event_type.value,
            "type_id": sanitize_pathname(self.event_id),
            "build_commit": self.build_commit,
            "commit": commit,
            "number": str(self.number),
            "status": self.status,
            "common_status": COMMON_STATUS_MAP[self.status].value,
        }

    def expand_path(self, path_template: str, variables: dict[str, str]) -> str:
        return expand_template(path_template, self.path_fields(), variables)

    @abstractmethod
    def download(self, path: Path) -> list[Path]:
        ...


class BuildLog(BuildAsset):
    pass


class Artifact(BuildAsset):
    pass


# These config-related classes need to go in this file to avoid a circular
# import issue:


class NoExtraModel(BaseModel, populate_by_name=True, extra="forbid"):
    pass


def literalize_str(v: Any, info: ValidationInfo) -> Any:
    if isinstance(v, str) and not info.data.get("regex"):
        v = r"\A" + re.escape(v) + r"\Z"
    return v


StrOrRegex = Annotated[re.Pattern, BeforeValidator(literalize_str)]


class WorkflowSpec(NoExtraModel):
    regex: bool = False
    # Workflow names are stored as compiled regexes regardless of whether
    # `regex` is true in order to keep type-checking simple.
    include: List[StrOrRegex] = Field(default_factory=lambda: [re.compile(".*")])
    exclude: List[StrOrRegex] = Field(default_factory=list)

    def match(self, wf_name: str) -> bool:
        return any(r.search(wf_name) for r in self.include) and not any(
            r.search(wf_name) for r in self.exclude
        )


class GHWorkflowSpec(WorkflowSpec):
    def match(self, wf_path: str) -> bool:
        return super().match(PurePosixPath(wf_path).name)
