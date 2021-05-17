from abc import ABC, abstractmethod
from collections import deque
from datetime import datetime, timezone
from enum import Enum
from functools import cached_property
import heapq
from io import BytesIO
import json
import logging
import os
from pathlib import Path
import re
from shutil import rmtree
import subprocess
import tempfile
from typing import Any, Dict, Iterator, List, Match, Optional, Pattern, Tuple
from urllib.parse import quote
from zipfile import ZipFile

import click
from click_loglevel import LogLevel
from datalad.api import Dataset
from dateutil.parser import isoparse
from github import Github
from github.Repository import Repository
from github.Workflow import Workflow
from github.WorkflowRun import WorkflowRun
from in_place import InPlace
from pydantic import BaseModel, Field, validator
import requests
from yaml import safe_load

from .util import expand_template

log = logging.getLogger("tinuous")

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


class CISystem(ABC, BaseModel):
    repo: str
    token: str
    since: datetime
    fetched: List[Tuple[datetime, bool]] = Field(default_factory=list)

    @staticmethod
    @abstractmethod
    def get_auth_token() -> str:
        ...

    @abstractmethod
    def get_assets(
        self, event_types: List[EventType], artifacts: bool = False
    ) -> Iterator["Asset"]:
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

    class Config:
        # <https://github.com/samuelcolvin/pydantic/issues/1241>
        arbitrary_types_allowed = True
        keep_untouched = (cached_property,)


class Asset(ABC, BaseModel):
    created_at: datetime
    event_type: EventType
    event_id: str
    commit: str
    number: int
    status: str

    def path_fields(self) -> Dict[str, str]:
        utc_date = self.created_at.astimezone(timezone.utc)
        return {
            "year": utc_date.strftime("%Y"),
            "month": utc_date.strftime("%m"),
            "day": utc_date.strftime("%d"),
            "hour": utc_date.strftime("%H"),
            "minute": utc_date.strftime("%M"),
            "second": utc_date.strftime("%S"),
            "type": self.event_type.value,
            "type_id": self.event_id,
            "commit": self.commit,
            "abbrev_commit": self.commit[:7],
            "number": str(self.number),
            "status": self.status,
            "common_status": COMMON_STATUS_MAP[self.status],
        }

    def expand_path(self, path_template: str, vars: Dict[str, str]) -> str:
        return expand_template(path_template, self.path_fields(), vars)

    @abstractmethod
    def download(self, path: Path) -> List[Path]:
        ...


class BuildLog(Asset):
    pass


class Artifact(Asset):
    pass


class GitHubActions(CISystem):
    workflows: Optional[List[str]] = None

    @staticmethod
    def get_auth_token() -> str:
        token = os.environ.get("GITHUB_TOKEN")
        if not token:
            r = subprocess.run(
                ["git", "config", "hub.oauthtoken"],
                stdout=subprocess.PIPE,
                universal_newlines=True,
            )
            if r.returncode != 0 or not r.stdout.strip():
                raise RuntimeError(
                    "GitHub OAuth token not set.  Set via GITHUB_TOKEN"
                    " environment variable or hub.oauthtoken Git config option."
                )
            token = r.stdout.strip()
        return token

    @cached_property
    def client(self) -> Github:
        return Github(self.token)

    @cached_property
    def dl_session(self) -> requests.Session:
        """
        `requests.Session` used for downloading resources and other actions not
        supported by Pygithub
        """
        s = requests.Session()
        s.headers["Authorization"] = f"token {self.token}"
        return s

    @cached_property
    def ghrepo(self) -> Repository:
        return self.client.get_repo(self.repo)

    def get_workflows(self) -> Iterator[Workflow]:
        if self.workflows is None:
            yield from self.ghrepo.get_workflows()
        else:
            for wffile in self.workflows:
                yield self.ghrepo.get_workflow(wffile)

    def get_assets(
        self, event_types: List[EventType], artifacts: bool = False
    ) -> Iterator["Asset"]:
        log.info("Fetching runs newer than %s", self.since)
        for wf in self.get_workflows():
            log.info("Fetching runs for workflow %s (%s)", wf.path, wf.name)
            for run in wf.get_runs():
                run_event = EventType.from_gh_event(run.event)
                ts = ensure_aware(run.created_at)
                if ts <= self.since:
                    break
                elif run.status != "completed":
                    log.info("Run %s not completed; skipping", run.run_number)
                    self.register_build(ts, False)
                else:
                    log.info("Found run %s", run.run_number)
                    self.register_build(ts, True)
                    if run_event in event_types:
                        event_id = self.get_event_id(run, run_event)
                        yield GHABuildLog.from_workflow_run(
                            self.dl_session, wf, run, run_event, event_id
                        )
                        if artifacts:
                            for name, download_url in self.get_artifacts(run):
                                yield GHAArtifact.from_workflow_run(
                                    self.dl_session,
                                    wf,
                                    run,
                                    run_event,
                                    event_id,
                                    name,
                                    download_url,
                                )
                    else:
                        log.info("Event type is %r; skipping", run.event)

    def get_event_id(self, run: WorkflowRun, event_type: EventType) -> str:
        if event_type is EventType.CRON:
            return ensure_aware(run.created_at).strftime("%Y%m%dT%H%M%S")
        elif event_type is EventType.PUSH:
            return run.head_branch
        elif event_type is EventType.PULL_REQUEST:
            if run.pull_requests:
                return str(run.pull_requests[0].number)
            else:
                # The experimental "List pull requests associated with a
                # commit" endpoint does not return data reliably enough to be
                # worth using, so we have to do an issue search for the
                # matching PR instead.
                try:
                    return str(
                        self.client.search_issues(
                            f"repo:{run.repository.full_name} is:pr {run.head_sha}",
                            sort="created",
                            order="asc",
                        )[0].number
                    )
                except IndexError:
                    return "UNK"
        else:
            raise AssertionError(f"Unhandled EventType: {event_type!r}")

    def get_artifacts(self, run: WorkflowRun) -> Iterator[Tuple[str, str]]:
        """ Yields each artifact as a (name, download_url) pair """
        url = run.artifacts_url
        while url is not None:
            r = self.dl_session.get(url)
            r.raise_for_status()
            for artifact in r.json()["artifacts"]:
                if not artifact["expired"]:
                    yield (artifact["name"], artifact["archive_download_url"])
            url = r.links.get("next", {}).get("url")

    def get_release_assets(self) -> Iterator["GHReleaseAsset"]:
        log.info("Fetching releases newer than %s", self.since)
        for rel in self.ghrepo.get_releases():
            if rel.draft:
                log.info("Release %s is draft; skipping", rel.tag_name)
                continue
            if rel.prerelease:
                log.info("Release %s is prerelease; skipping", rel.tag_name)
                continue
            ts = ensure_aware(rel.published_at)
            if ts <= self.since:
                continue
            self.register_build(ts, True)  # TODO: Set to False for drafts?
            log.info("Found release %s", rel.tag_name)
            r = self.dl_session.get(
                f"https://api.github.com/repos/{self.repo}/git/refs/tags/{rel.tag_name}"
            )
            r.raise_for_status()
            tagobj = r.json()["object"]
            if tagobj["type"] == "commit":
                commit = tagobj["sha"]
            elif tagobj["type"] == "tag":
                r = self.dl_session.get(tagobj["url"])
                r.raise_for_status()
                commit = r.json()["object"]["sha"]
            else:
                raise RuntimeError(
                    f"Unexpected type for tag {rel.tag_name}: {tagobj['type']!r}"
                )
            for asset in rel.get_assets():
                yield GHReleaseAsset(
                    session=self.dl_session,
                    published_at=ts,
                    tag_name=rel.tag_name,
                    commit=commit,
                    name=asset.name,
                    download_url=asset.browser_download_url,
                )


class GHAAsset(Asset):
    session: requests.Session
    workflow_name: str
    workflow_file: str
    run_id: int

    class Config:
        # To allow requests.Session:
        arbitrary_types_allowed = True

    def path_fields(self) -> Dict[str, str]:
        fields = super().path_fields()
        fields.update(
            {
                "ci": "github",
                "wf_name": re.sub(r'[\x5C/<>:|"?*]', "_", self.workflow_name),
                "wf_file": self.workflow_file,
                "run_id": str(self.run_id),
            }
        )
        return fields


class GHABuildLog(GHAAsset, BuildLog):
    logs_url: str

    @classmethod
    def from_workflow_run(
        cls,
        session: requests.Session,
        workflow: Workflow,
        run: WorkflowRun,
        event_type: EventType,
        event_id: str,
    ) -> "GHABuildLog":
        return cls(
            session=session,
            logs_url=run.logs_url,
            created_at=ensure_aware(run.created_at),
            event_type=event_type,
            event_id=event_id,
            commit=run.head_sha,
            workflow_name=workflow.name,
            workflow_file=workflow.path.split("/")[-1],
            number=run.run_number,
            run_id=run.id,
            status=run.conclusion,
        )

    def download(self, path: Path) -> List[Path]:
        path.mkdir(parents=True, exist_ok=True)
        if any(path.iterdir()):
            log.info(
                "Logs for %s (%s) #%s already downloaded to %s; skipping",
                self.workflow_file,
                self.workflow_name,
                self.number,
                path,
            )
            return []
        log.info(
            "Downloading logs for %s (%s) #%s to %s",
            self.workflow_file,
            self.workflow_name,
            self.number,
            path,
        )
        r = self.session.get(self.logs_url)
        if r.status_code == 404:
            # This can happen when a workflow failed to run due to, say, a
            # syntax error.
            log.error("Request for logs returned 404; skipping")
            return []
        r.raise_for_status()
        try:
            with BytesIO(r.content) as blob, ZipFile(blob) as zf:
                zf.extractall(path)
        except BaseException:
            rmtree(path)
            raise
        return list(path.rglob("*.txt"))


class GHAArtifact(GHAAsset, Artifact):
    name: str
    download_url: str

    @classmethod
    def from_workflow_run(
        cls,
        session: requests.Session,
        workflow: Workflow,
        run: WorkflowRun,
        event_type: EventType,
        event_id: str,
        name: str,
        download_url: str,
    ) -> "GHAArtifact":
        return cls(
            session=session,
            created_at=ensure_aware(run.created_at),
            event_type=event_type,
            event_id=event_id,
            commit=run.head_sha,
            workflow_name=workflow.name,
            workflow_file=workflow.path.split("/")[-1],
            number=run.run_number,
            run_id=run.id,
            status=run.conclusion,
            name=name,
            download_url=download_url,
        )

    def download(self, path: Path) -> List[Path]:
        target_dir = path / self.name
        target_dir.mkdir(parents=True, exist_ok=True)
        if any(target_dir.iterdir()):
            log.info(
                "Asset %s from %s (%s) #%s already downloaded to %s; skipping",
                self.name,
                self.workflow_file,
                self.workflow_name,
                self.number,
                path,
            )
            return []
        log.info(
            "Downloading asset %s for %s (%s) #%s to %s",
            self.name,
            self.workflow_file,
            self.workflow_name,
            self.number,
            path,
        )
        r = self.session.get(self.download_url, stream=True)
        r.raise_for_status()
        fd, fpath = tempfile.mkstemp()
        os.close(fd)
        zippath = Path(fpath)
        try:
            stream_to_file(r, zippath)
            try:
                with ZipFile(zippath) as zf:
                    zf.extractall(target_dir)
            except BaseException:
                rmtree(target_dir)
                raise
        finally:
            zippath.unlink(missing_ok=True)
        return list(iterfiles(target_dir))


class GHReleaseAsset(BaseModel):
    session: requests.Session
    published_at: datetime
    tag_name: str
    commit: str
    name: str
    download_url: str

    class Config:
        # To allow requests.Session:
        arbitrary_types_allowed = True

    def path_fields(self) -> Dict[str, str]:
        utc_date = self.published_at.astimezone(timezone.utc)
        return {
            "year": utc_date.strftime("%Y"),
            "month": utc_date.strftime("%m"),
            "day": utc_date.strftime("%d"),
            "hour": utc_date.strftime("%H"),
            "minute": utc_date.strftime("%M"),
            "second": utc_date.strftime("%S"),
            "ci": "github",
            "type": "release",
            "type_id": self.tag_name,
            "commit": self.commit,
            "abbrev_commit": self.commit[:7],
        }

    def expand_path(self, path_template: str, vars: Dict[str, str]) -> str:
        return expand_template(path_template, self.path_fields(), vars)

    def download(self, path: Path) -> List[Path]:
        target = path / self.name
        if target.exists():
            log.info(
                "Asset %s for release %s already downloaded to %s; skipping",
                self.name,
                self.tag_name,
                target,
            )
            return []
        path.mkdir(parents=True, exist_ok=True)
        log.info(
            "Downloading asset %s for release %s to %s",
            self.name,
            self.tag_name,
            target,
        )
        r = self.session.get(self.download_url, stream=True)
        stream_to_file(r, target)
        return [target]


class Travis(CISystem):
    @staticmethod
    def get_auth_token() -> str:
        token = os.environ.get("TRAVIS_TOKEN")
        if not token:
            try:
                r = subprocess.run(
                    ["travis", "token", "--com", "--no-interactive"],
                    stdout=subprocess.PIPE,
                    universal_newlines=True,
                )
            except FileNotFoundError:
                raise RuntimeError(
                    "Travis token not set.  Set via TRAVIS_TOKEN environment"
                    " variable or log in with `travis` client.  See"
                    " <https://github.com/con/tinuous#travis> for more"
                    " information."
                )
            if r.returncode != 0 or not r.stdout.strip():
                raise RuntimeError(
                    "Travis token not set.  Set via TRAVIS_TOKEN environment"
                    " variable or log in with `travis` client.  See"
                    " <https://github.com/con/tinuous#travis> for more"
                    " information."
                )
            token = r.stdout.strip()
        return token

    @cached_property
    def session(self) -> requests.Session:
        s = requests.Session()
        s.headers["Travis-API-Version"] = "3"
        s.headers["Authorization"] = f"token {self.token}"
        return s

    def get(self, path: str, **kwargs: Any) -> requests.Response:
        url = "https://api.travis-ci.com/" + path.lstrip("/")
        r = self.session.get(url, **kwargs)
        assert isinstance(r, requests.Response)
        r.raise_for_status()
        return r

    def paginate(
        self, path: str, params: Optional[Dict[str, str]] = None
    ) -> Iterator[dict]:
        while True:
            data = self.get(path, params=params).json()
            yield from data[data["@type"]]
            try:
                path = data["@pagination"]["next"]["@href"]
            except (KeyError, TypeError):
                break
            params = None

    def get_assets(
        self, event_types: List[EventType], artifacts: bool = False  # noqa: U100
    ) -> Iterator["Asset"]:
        log.info("Fetching builds newer than %s", self.since)
        for build in self.paginate(
            f"/repo/{quote(self.repo, safe='')}/builds",
            params={"include": "build.jobs"},
        ):
            run_event = EventType.from_travis_event(build["event_type"])
            if build["started_at"] is None:
                ### TODO: If there are any builds with a higher number that
                ### have already started and finished, this can lead to the
                ### skipped build being permanently skipped.
                log.info("Build %s not started; skipping", build["number"])
                continue
            else:
                ts = isoparse(build["started_at"])
            if ts <= self.since:
                break
            elif build["finished_at"] is None:
                log.info("Build %s not completed; skipping", build["number"])
                self.register_build(ts, False)
            else:
                log.info("Found build %s", build["number"])
                self.register_build(ts, True)
                if run_event in event_types:
                    for job in build["jobs"]:
                        yield TravisJobLog.from_job(self, build, job)
                else:
                    log.info("Event type is %r; skipping", build["event_type"])


class TravisJobLog(BuildLog):
    client: Travis
    job: str
    job_id: int

    @classmethod
    def from_job(
        cls,
        client: Travis,
        build: Dict[str, Any],
        job: Dict[str, Any],
    ) -> "TravisJobLog":
        created_at = isoparse(build["started_at"])
        event = EventType.from_travis_event(build["event_type"])
        if event is None:
            raise ValueError(f"Build has unknown event type {build['event_type']!r}")
        event_id: str
        if event is EventType.CRON:
            event_id = created_at.strftime("%Y%m%dT%H%M%S")
        elif event is EventType.PUSH:
            event_id = build["branch"]["name"]
        elif event is EventType.PULL_REQUEST:
            event_id = str(build["pull_request_number"])
        else:
            raise AssertionError(f"Unhandled EventType: {event!r}")
        return cls(
            client=client,
            created_at=created_at,
            event_type=event,
            event_id=event_id,
            commit=build["commit"]["sha"],
            number=int(build["number"]),
            job=removeprefix(job["number"], f"{build['number']}."),
            job_id=job["id"],
            status=job["state"],
        )

    def path_fields(self) -> Dict[str, str]:
        fields = super().path_fields()
        fields.update(
            {
                "ci": "travis",
                "job": self.job,
            }
        )
        return fields

    def download(self, path: Path) -> List[Path]:
        if path.exists():
            log.info(
                "Logs for job %s.%s already downloaded to %s; skipping",
                self.number,
                self.job,
                path,
            )
            return []
        path.parent.mkdir(parents=True, exist_ok=True)
        log.info(
            "Downloading logs for job %s.%s to %s",
            self.number,
            self.job,
            path,
        )
        r = self.client.get(f"/job/{self.job_id}/log.txt", stream=True)
        stream_to_file(r, path)
        return [path]


class Appveyor(CISystem):
    accountName: str
    projectSlug: Optional[str]

    @staticmethod
    def get_auth_token() -> str:
        token = os.environ.get("APPVEYOR_TOKEN")
        if not token:
            raise RuntimeError(
                "Appveyor API key not set.  Set via APPVEYOR_TOKEN environment"
                " variable."
            )
        return token

    @property
    def repo_slug(self) -> str:
        if self.projectSlug is None:
            return self.repo.split("/")[1]
        else:
            return self.projectSlug

    @cached_property
    def session(self) -> requests.Session:
        s = requests.Session()
        s.headers["Authorization"] = f"Bearer {self.token}"
        return s

    def get(self, path: str, **kwargs: Any) -> requests.Response:
        url = "https://ci.appveyor.com/" + path.lstrip("/")
        r = self.session.get(url, **kwargs)
        r.raise_for_status()
        return r

    def get_builds(self) -> Iterator[dict]:
        params = {"recordsNumber": 20}
        while True:
            data = self.get(
                f"/api/projects/{self.accountName}/{self.repo_slug}/history",
                params=params,
            ).json()
            if data.get("builds"):
                yield from data["builds"]
                params["startBuildId"] = data["builds"][-1]["buildId"]
            else:
                break

    def get_assets(
        self, event_types: List[EventType], artifacts: bool = False  # noqa: U100
    ) -> Iterator["Asset"]:
        log.info("Fetching runs newer than %s", self.since)
        for build in self.get_builds():
            if build.get("pullRequestId"):
                run_event = EventType.PULL_REQUEST
            else:
                run_event = EventType.PUSH
            ts = isoparse(build["created"])
            if ts <= self.since:
                break
            elif build.get("finished") is None:
                log.info("Build %s not completed; skipping", build["buildNumber"])
                self.register_build(ts, False)
            else:
                log.info("Found build %s", build["buildNumber"])
                self.register_build(ts, True)
                if run_event in event_types:
                    for job in self.get(
                        f"/api/projects/{self.accountName}/{self.repo_slug}"
                        f"/build/{build['version']}"
                    ).json()["build"]["jobs"]:
                        yield AppveyorJobLog.from_job(self, build, job)
                else:
                    log.info("Event type is %r; skipping", run_event.value)


class AppveyorJobLog(BuildLog):
    client: Appveyor
    job: str

    @classmethod
    def from_job(
        cls,
        client: Appveyor,
        build: Dict[str, Any],
        job: Dict[str, Any],
    ) -> "AppveyorJobLog":
        created_at = isoparse(build["created"])
        if build.get("pullRequestId"):
            event = EventType.PULL_REQUEST
            event_id = build["pullRequestId"]
        else:
            event = EventType.PUSH
            event_id = build["branch"]
        return cls(
            client=client,
            created_at=created_at,
            event_type=event,
            event_id=event_id,
            commit=build["commitId"],
            number=build["buildNumber"],
            job=job["jobId"],
            status=job["status"],
        )

    def path_fields(self) -> Dict[str, str]:
        fields = super().path_fields()
        fields.update(
            {
                "ci": "appveyor",
                "job": self.job,
            }
        )
        return fields

    def download(self, path: Path) -> List[Path]:
        if path.exists():
            log.info(
                "Logs for build %s, job %s already downloaded to %s; skipping",
                self.number,
                self.job,
                path,
            )
            return []
        path.parent.mkdir(parents=True, exist_ok=True)
        log.info(
            "Downloading logs for build %s, job %s to %s",
            self.number,
            self.job,
            path,
        )
        r = self.client.get(f"/api/buildjobs/{self.job}/log", stream=True)
        stream_to_file(r, path)
        return [path]


class NoExtraModel(BaseModel):
    class Config:
        allow_population_by_field_name = True
        extra = "forbid"


class CIConfig(NoExtraModel, ABC):
    path: str

    @staticmethod
    @abstractmethod
    def get_auth_token() -> str:
        ...

    @abstractmethod
    def get_system(self, repo: str, since: datetime, token: str) -> CISystem:
        ...


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
        if (g := self.github) is not None:
            yield ("github", g)
        if (t := self.travis) is not None:
            yield ("travis", t)
        if (a := self.appveyor) is not None:
            yield ("appveyor", a)


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


@click.group()
@click.option(
    "-c",
    "--config",
    type=click.Path(exists=True, dir_okay=False),
    default="config.yml",
    help="Read configuration from the given file",
    show_default=True,
)
@click.option(
    "-l",
    "--log-level",
    type=LogLevel(),
    default="INFO",
    help="Set logging level",
    show_default=True,
)
@click.pass_context
def main(ctx: click.Context, config: str, log_level: int) -> None:
    """ Download build logs from GitHub Actions, Travis, and Appveyor """
    logging.basicConfig(
        format="%(asctime)s [%(levelname)-8s] %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
        level=log_level,
    )
    with open(config) as fp:
        ctx.obj = Config.parse_obj(safe_load(fp))


@main.command()
@click.option(
    "--sanitize-secrets",
    is_flag=True,
    help="Sanitize strings matching secret patterns",
)
@click.option(
    "-S",
    "--state",
    type=click.Path(dir_okay=False, writable=True),
    default=".dlstate.json",
    help="Store program state in the given file",
    show_default=True,
)
@click.pass_obj
def fetch(cfg: Config, state: str, sanitize_secrets: bool) -> None:
    """ Download logs """
    if sanitize_secrets and not cfg.secrets:
        log.warning("--sanitize-secrets set but no secrets given in configuration")
    since_stamps: Dict[str, str]
    try:
        with open(state) as fp:
            since_stamps = json.load(fp)
    except FileNotFoundError:
        since_stamps = {}
    # Fetch tokens early in order to catch failures early:
    tokens: Dict[str, str] = {}
    for name, cicfg in cfg.ci.items():
        tokens[name] = cicfg.get_auth_token()
    ds = Dataset(os.curdir)
    if cfg.datalad.enabled and not ds.is_installed():
        ds.create(force=True, cfg_proc=cfg.datalad.cfg_proc)
    logs_added = 0
    artifacts_added = 0
    relassets_added = 0
    for name, cicfg in cfg.ci.items():
        get_artifacts = getattr(cicfg, "artifacts_path", None) is not None
        log.info("Fetching resources from %s", name)
        try:
            since = datetime.fromisoformat(since_stamps[name])
        except KeyError:
            since = cfg.since
        ci = cicfg.get_system(repo=cfg.repo, since=since, token=tokens[name])
        for obj in ci.get_assets(cfg.types, artifacts=get_artifacts):
            if isinstance(obj, BuildLog):
                path = obj.expand_path(cicfg.path, cfg.vars)
            elif isinstance(obj, Artifact):
                assert get_artifacts
                path = obj.expand_path(
                    cicfg.artifacts_path,  # type: ignore[attr-defined]
                    cfg.vars,
                )
            else:
                raise AssertionError(f"Unexpected asset type {type(obj).__name__}")
            if cfg.datalad.enabled:
                ensure_datalad(ds, path, cfg.datalad.cfg_proc)
            paths = obj.download(Path(path))
            if isinstance(obj, BuildLog):
                logs_added += len(paths)
                if sanitize_secrets and cfg.secrets:
                    for p in paths:
                        sanitize(p, cfg.secrets, cfg.allow_secrets_regex)
            elif isinstance(obj, Artifact):
                artifacts_added += len(paths)
        if isinstance(cicfg, GitHubConfig) and cicfg.releases_path is not None:
            assert isinstance(ci, GitHubActions)
            for asset in ci.get_release_assets():
                path = asset.expand_path(cicfg.releases_path, cfg.vars)
                if cfg.datalad.enabled:
                    ensure_datalad(ds, path, cfg.datalad.cfg_proc)
                paths = asset.download(Path(path))
                relassets_added += len(paths)
        since_stamps[name] = ci.new_since().isoformat()
        log.debug("%s timestamp floor updated to %s", name, since_stamps[name])
    with open(state, "w") as fp:
        json.dump(since_stamps, fp)
    log.info("%d logs downloaded", logs_added)
    log.info("%d artifacts downloaded", artifacts_added)
    log.info("%d release assets downloaded", relassets_added)
    if cfg.datalad.enabled and (logs_added or artifacts_added or relassets_added):
        msg = f"[tinuous] {logs_added} logs added"
        if artifacts_added:
            msg += f", {artifacts_added} artifacts added"
        if relassets_added:
            msg += f", {relassets_added} release assets added"
        ds.save(recursive=True, message=msg)


@main.command("sanitize")
@click.argument(
    "path", type=click.Path(exists=True, dir_okay=False, writable=True), nargs=-1
)
@click.pass_obj
def sanitize_cmd(cfg: Config, path: List[str]) -> None:
    """ Sanitize secrets in logs """
    for p in path:
        sanitize(Path(p), cfg.secrets, cfg.allow_secrets_regex)


def ensure_aware(dt: datetime) -> datetime:
    # Pygithub returns naïve datetimes for timestamps with a "Z" suffix.  Until
    # that's fixed <https://github.com/PyGithub/PyGithub/pull/1831>, we need to
    # make such datetimes timezone-aware manually.
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def removeprefix(s: str, prefix: str) -> str:
    n = len(prefix)
    return s[n:] if s[:n] == prefix else s


def stream_to_file(r: requests.Response, p: Path) -> None:
    try:
        with p.open("wb") as fp:
            for chunk in r.iter_content(chunk_size=8192):
                fp.write(chunk)
    except BaseException:
        p.unlink(missing_ok=True)
        raise


def sanitize(
    p: Path, secrets: Dict[str, Pattern], allow_secrets: Optional[Pattern]
) -> None:
    def replace(m: Match) -> str:
        s = m.group()
        assert isinstance(s, str)
        if allow_secrets is not None and allow_secrets.search(s):
            return s
        else:
            return "*" * len(s)

    log.info("Sanitizing %s", p)
    with InPlace(p, mode="t", encoding="utf-8", newline="") as fp:
        for i, line in enumerate(fp, start=1):
            for name, rgx in secrets.items():
                newline = rgx.sub(replace, line)
                if newline != line:
                    log.info("Found %s secret on line %d", name, i)
                line = newline
            fp.write(line)


def iterfiles(dirpath: Path) -> Iterator[Path]:
    dirs = deque([dirpath])
    while dirs:
        d = dirs.popleft()
        for p in d.iterdir():
            if p.is_dir():
                dirs.append(p)
            else:
                yield p


def ensure_datalad(ds: Dataset, path: str, cfg_proc: Optional[str]) -> None:
    dspaths = path.split("//")
    if "" in dspaths:
        raise click.UsageError("Path contains empty '//'-delimited segment")
    for i in range(1, len(dspaths)):
        dsp = "/".join(dspaths[:i])
        if not Path(dsp).exists():
            ds.create(dsp, cfg_proc=cfg_proc)


if __name__ == "__main__":
    main()
