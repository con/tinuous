from abc import ABC, abstractmethod
from dataclasses import dataclass, field
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
from typing import Any, Dict, Iterator, List, Match, Optional, Pattern, Tuple
from urllib.parse import quote
from zipfile import ZipFile

import click
from click_loglevel import LogLevel
from datalad.api import Dataset
from dateutil.parser import isoparse
from github import Github
from github.Workflow import Workflow
from github.WorkflowRun import WorkflowRun
from in_place import InPlace
from pydantic import BaseModel, Field, validator
import requests
from yaml import safe_load

log = logging.getLogger("tinuous")

COMMON_STATUS_MAP = {
    "success": "success",
    "passed": "success",
    "failure": "failed",
    "failed": "failed",
    "errored": "errored",
    "timed_out": "errored",
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


# We can't make this an ABC due to <https://github.com/python/mypy/issues/5374>
@dataclass
class CISystem:
    repo: str
    token: str = field(repr=False)
    since: datetime
    fetched: List[Tuple[datetime, bool]] = field(init=False, default_factory=list)

    @staticmethod
    def get_auth_token() -> str:
        raise NotImplementedError

    def get_build_logs(self, event_types: List[EventType]) -> Iterator["BuildLog"]:
        raise NotImplementedError

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


# We can't make this an ABC due to <https://github.com/python/mypy/issues/5374>
@dataclass
class BuildLog:
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
            "number": str(self.number),
            "status": self.status,
            "common_status": COMMON_STATUS_MAP[self.status],
        }

    def expand_path(self, path_template: str) -> str:
        return path_template.format_map(self.path_fields())

    def download(self, path: Path) -> List[Path]:
        raise NotImplementedError


@dataclass
class GitHubActions(CISystem):
    workflows: List[str]

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
        s = requests.Session()
        s.headers["Authorization"] = f"token {self.token}"
        return s

    def get_build_logs(self, event_types: List[EventType]) -> Iterator["GHABuildLog"]:
        log.info("Fetching runs newer than %s", self.since)
        repo = self.client.get_repo(self.repo)
        for wffile in self.workflows:
            wf = repo.get_workflow(wffile)
            log.info("Fetching runs for workflow %s (%s)", wffile, wf.name)
            for run in wf.get_runs():  # type: ignore[call-arg]
                # <https://github.com/PyGithub/PyGithub/pull/1857>
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
                        yield GHABuildLog.from_workflow_run(self.dl_session, wf, run)
                    else:
                        log.info("Event type is %r; skipping", run.event)


@dataclass
class GHABuildLog(BuildLog):
    session: requests.Session
    logs_url: str
    workflow_name: str
    workflow_file: str
    run_id: int

    @classmethod
    def from_workflow_run(
        cls,
        session: requests.Session,
        workflow: Workflow,
        run: WorkflowRun,
    ) -> "GHABuildLog":
        run_event = EventType.from_gh_event(run.event)
        if run_event is None:
            raise ValueError(f"Run has unknown event type {run.event!r}")
        event_id: str
        if run_event is EventType.CRON:
            event_id = ensure_aware(run.created_at).strftime("%Y%m%dT%H%M%S")
        elif run_event is EventType.PUSH:
            event_id = run.head_branch
        elif run_event is EventType.PULL_REQUEST:
            if run.pull_requests:
                event_id = str(run.pull_requests[0].number)
            else:
                event_id = "UNK"
        else:
            raise AssertionError(f"Unhandled EventType: {run_event!r}")
        return cls(
            session=session,
            logs_url=run.logs_url,
            created_at=ensure_aware(run.created_at),
            event_type=run_event,
            event_id=event_id,
            commit=run.head_sha,
            workflow_name=workflow.name,
            workflow_file=workflow.path.split("/")[-1],
            number=run.run_number,
            run_id=run.id,
            status=run.conclusion,
        )

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
        r.raise_for_status()
        try:
            with BytesIO(r.content) as blob, ZipFile(blob) as zf:
                zf.extractall(path)
        except BaseException:
            rmtree(path)
            raise
        return list(path.rglob("*.txt"))


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

    def get_build_logs(self, event_types: List[EventType]) -> Iterator["TravisJobLog"]:
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


@dataclass
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


@dataclass
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

    def get_build_logs(
        self, event_types: List[EventType]
    ) -> Iterator["AppveyorJobLog"]:
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


@dataclass
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
    workflows: List[str]

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
    ci: CIConfigDict
    since: datetime
    types: List[EventType]
    secrets: Dict[str, Pattern] = Field(default_factory=dict)
    allow_secrets_regex: Optional[Pattern] = Field(None, alias="allow-secrets-regex")
    datalad: DataladConfig = Field(default_factory=DataladConfig)

    @validator("repo")
    def _validate_repo(cls, v: str) -> str:  # noqa: B902
        if not re.fullmatch(r"[^/]+/[^/]+", v):
            raise ValueError("Repo must be in the form 'OWNER/NAME'")
        return v

    @validator("since")
    def _validate_since(cls, v: datetime) -> datetime:  # noqa: B902
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
    added = 0
    for name, cicfg in cfg.ci.items():
        log.info("Fetching logs from %s", name)
        try:
            since = datetime.fromisoformat(since_stamps[name])
        except KeyError:
            since = cfg.since
        ci = cicfg.get_system(repo=cfg.repo, since=since, token=tokens[name])
        for bl in ci.get_build_logs(cfg.types):
            path = bl.expand_path(cicfg.path)
            if cfg.datalad.enabled:
                dspaths = path.split("//")
                if "" in dspaths:
                    raise click.UsageError("Path contains empty '//'-delimited segment")
                for i in range(1, len(dspaths)):
                    dsp = "/".join(dspaths[:i])
                    if not Path(dsp).exists():
                        ds.create(dsp, cfg_proc=cfg.datalad.cfg_proc)
            logs = bl.download(Path(path))
            added += len(logs)
            if sanitize_secrets and cfg.secrets:
                for p in logs:
                    sanitize(p, cfg.secrets, cfg.allow_secrets_regex)
        since_stamps[name] = ci.new_since().isoformat()
        log.debug("%s timestamp floor updated to %s", name, since_stamps[name])
    with open(state, "w") as fp:
        json.dump(since_stamps, fp)
    log.info("%d logs downloaded", added)
    if cfg.datalad.enabled and added:
        ds.save(recursive=True, message=f"[tinuous] {added} logs added")


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


if __name__ == "__main__":
    main()
