#!/usr/bin/env python3
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
import subprocess
from typing import Any, Dict, Iterator, List, Optional, Tuple, Type
from urllib.parse import quote
from zipfile import ZipFile

import cfgv
import click
from click_loglevel import LogLevel
from dateutil.parser import isoparse
from github import Github
from github.Workflow import Workflow
from github.WorkflowRun import WorkflowRun
import requests
from yaml import safe_load

log = logging.getLogger("download-logs")


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


@dataclass
class CISystem(ABC):
    repo: str
    token: str = field(repr=False)
    since: datetime
    fetched: List[Tuple[datetime, bool]] = field(init=False, default_factory=list)

    @staticmethod
    @abstractmethod
    def get_auth_token() -> str:
        ...

    @abstractmethod
    def get_build_logs(self, event_types: List[EventType]) -> Iterator["BuildLog"]:
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
                        yield GHABuildLog.from_workflow_run(self.dl_session, wf, run)
                    else:
                        log.info("Event type is %r; skipping", run.event)

@dataclass
class BuildLog(ABC):
    created_at: datetime
    event_type: EventType
    event_id: str
    commit: str
    number: int

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
        }

    @abstractmethod
    def download(self, path_template: str) -> None:
        ...


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
        )

    def path_fields(self) -> Dict[str, str]:
        fields = super().path_fields()
        fields.update({
            "ci": "github",
            "wf_name": re.sub(r'[\x5C/<>:|"?*]', "_", self.workflow_name),
            "wf_file": self.workflow_file,
            "run_id": str(self.run_id),
        })
        return fields

    def download(self, path_template: str) -> None:
        p = Path(path_template.format_map(self.path_fields()))
        p.mkdir(parents=True, exist_ok=True)
        log.info(
            "Downloading logs for %s (%s) #%s to %s",
            self.workflow_file,
            self.workflow_name,
            self.number,
            p,
        )
        r = self.session.get(self.logs_url)
        r.raise_for_status()
        with BytesIO(r.content) as blob, ZipFile(blob) as zf:
            zf.extractall(p)


class Travis(CISystem):
    @staticmethod
    def get_auth_token() -> str:
        token = os.environ.get("TRAVIS_TOKEN")
        if not token:
            r = subprocess.run(
                ["travis", "token", "--com", "--no-interactive"],
                stdout=subprocess.PIPE,
                universal_newlines=True,
            )
            if r.returncode != 0 or not r.stdout.strip():
                raise RuntimeError(
                    "Travis token not set.  Set via TRAVIS_TOKEN environment"
                    " variable or log in with `travis` client."
                )
            token = r.stdout.strip()
        return token

    @cached_property
    def session(self):
        s = requests.Session()
        s.headers["Travis-API-Version"] = "3"
        s.headers["Authorization"] = f"token {self.token}"
        return s

    def get(self, path, **kwargs):
        url = "https://api.travis-ci.com/" + path.lstrip("/")
        r = self.session.get(url, **kwargs)
        r.raise_for_status()
        return r

    def paginate(self, path, params=None):
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
        )

    def path_fields(self) -> Dict[str, str]:
        fields = super().path_fields()
        fields.update({
            "ci": "travis",
            "job": self.job,
        })
        return fields

    def download(self, path_template: str) -> None:
        p = Path(path_template.format_map(self.path_fields()))
        p.parent.mkdir(parents=True, exist_ok=True)
        log.info(
            "Downloading logs for job %s.%s to %s",
            self.number,
            self.job,
            p,
        )
        r = self.client.get(f"/job/{self.job_id}/log.txt", stream=True)
        with p.open("wb") as fp:
            for chunk in r.iter_content(chunk_size=8192):
                fp.write(chunk)


CI_SYSTEMS: Dict[str, Type[CISystem]] = {
    "github": GitHubActions,
    "travis": Travis,
}


def check_repo(v: Any) -> None:
    if not isinstance(v, str):
        raise cfgv.ValidationError(f"Expected str, got {type(v).__name__}")
    elif not re.fullmatch(r"[^/]+/[^/]+", v):
        raise cfgv.ValidationError("Repo must be in the form 'OWNER/NAME'")


CONFIG_SCHEMA = cfgv.Map(
    "Config",
    None,
    cfgv.NoAdditionalKeys(["repo", "ci", "since", "types"]),
    cfgv.Required("repo", check_repo),
    cfgv.RequiredRecurse(
        "ci",
        cfgv.Map(
            "ci",
            None,
            cfgv.NoAdditionalKeys(list(CI_SYSTEMS.keys())),
            cfgv.OptionalRecurse(
                "github",
                cfgv.Map(
                    "ci.github",
                    None,
                    cfgv.NoAdditionalKeys(["path", "workflows"]),
                    cfgv.Required("path", cfgv.check_string),
                    cfgv.Required("workflows", cfgv.check_array(cfgv.check_string)),
                ),
                {},
            ),
            cfgv.OptionalRecurse(
                "travis",
                cfgv.Map(
                    "ci.travis",
                    None,
                    cfgv.NoAdditionalKeys(["path"]),
                    cfgv.Required("path", cfgv.check_string),
                ),
                {},
            ),
        ),
    ),
    cfgv.Required("since", cfgv.check_type(datetime)),
    cfgv.Required(
        "types", cfgv.check_array(cfgv.check_one_of([e.value for e in EventType]))
    ),
)


@click.command()
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
    default=logging.INFO,
    help="Set logging level [default: INFO]",
)
@click.option(
    "-S",
    "--state",
    type=click.Path(dir_okay=False, writable=True),
    default=".dlstate.json",
    help="Store program state in the given file",
    show_default=True,
)
def main(config, state, log_level):
    logging.basicConfig(
        format="%(asctime)s [%(levelname)-8s] %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
        level=log_level,
    )
    cfg = cfgv.load_from_filename(
        config,
        schema=CONFIG_SCHEMA,
        load_strategy=safe_load,
    )
    event_types = [EventType(e) for e in cfg["types"]]
    try:
        with open(state) as fp:
            since_stamps = json.load(fp)
    except FileNotFoundError:
        since_stamps = {}
    for name, cls in CI_SYSTEMS.items():
        if cfg["ci"][name]:
            log.info("Fetching logs from %s", name)
            path_template = cfg["ci"][name].pop("path")
            try:
                since = datetime.fromisoformat(since_stamps[name])
            except KeyError:
                since = cfg["since"]
            ci = cls(
                repo=cfg["repo"],
                since=since,
                token=cls.get_auth_token(),
                **cfg["ci"][name],
            )
            for bl in ci.get_build_logs(event_types):
                bl.download(path_template)
            since_stamps[name] = ci.new_since().isoformat()
            log.debug("%s timestamp floor updated to %s", since_stamps[name])
    with open(state, "w") as fp:
        json.dump(since_stamps, fp)


def ensure_aware(dt: datetime) -> datetime:
    # Pygithub returns naïve datetimes for timestamps with a "Z" suffix.  Until
    # that's fixed, we need to make such datetimes timezone-aware manually.
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt

def removeprefix(s: str, prefix: str) -> str:
    n = len(prefix)
    return s[n:] if s[:n] == prefix else s

if __name__ == "__main__":
    main()
