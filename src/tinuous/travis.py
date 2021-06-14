from functools import cached_property
import os
from pathlib import Path
import subprocess
from typing import Any, Dict, Iterator, List, Optional
from urllib.parse import quote

from dateutil.parser import isoparse
from github import Github
from github.GithubException import UnknownObjectException
from github.Repository import Repository

from .base import APIClient, BuildAsset, BuildLog, CISystem, EventType
from .util import get_github_token, log, removeprefix


class Travis(CISystem):
    gh_token: str

    @staticmethod
    def get_auth_tokens() -> Dict[str, str]:
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
        return {"travis": token, "github": get_github_token()}

    @cached_property
    def client(self) -> APIClient:
        return APIClient(
            "https://api.travis-ci.com",
            {
                "Travis-API-Version": "3",
                "Authorization": f"token {self.token}",
            },
        )

    @cached_property
    def ghrepo(self) -> Repository:
        return Github(self.gh_token).get_repo(self.repo)

    def paginate(
        self, path: str, params: Optional[Dict[str, str]] = None
    ) -> Iterator[dict]:
        while True:
            data = self.client.get(path, params=params).json()
            yield from data[data["@type"]]
            try:
                path = data["@pagination"]["next"]["@href"]
            except (KeyError, TypeError):
                break
            params = None

    def get_build_assets(
        self, event_types: List[EventType], logs: bool, artifacts: bool  # noqa: U100
    ) -> Iterator["BuildAsset"]:
        if not logs:
            log.debug("No assets requested for Travis builds")
            return
        log.info("Fetching builds newer than %s", self.since)
        if self.until is not None:
            log.info("Skipping builds newer than %s", self.until)
        for build in self.paginate(
            f"/repo/{quote(self.repo, safe='')}/builds",
            params={"include": "build.jobs"},
        ):
            event_type = EventType.from_travis_event(build["event_type"])
            if event_type is None:
                raise ValueError(
                    f"Build has unknown event type {build['event_type']!r}"
                )
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
            elif self.until is not None and ts > self.until:
                log.info("Build %s is too new; skipping", build["number"])
            elif build["finished_at"] is None:
                log.info("Build %s not completed; skipping", build["number"])
                self.register_build(ts, False)
            else:
                log.info("Found build %s", build["number"])
                self.register_build(ts, True)
                if event_type in event_types:
                    commit = self.get_commit(build, event_type)
                    for i, job in enumerate(build["jobs"], start=1):
                        yield TravisJobLog.from_job(
                            self.client, build, job, i, commit, event_type
                        )
                else:
                    log.info("Event type is %r; skipping", build["event_type"])

    def get_commit(self, build: Dict[str, Any], event_type: EventType) -> Optional[str]:
        if event_type in (EventType.CRON, EventType.MANUAL, EventType.PUSH):
            commit = build["commit"]["sha"]
            assert isinstance(commit, str)
            return commit
        elif event_type is EventType.PULL_REQUEST:
            # UNVERIFIED ASSUMPTION: The second parent of an autogenerated
            # merge commit is the commit that was the PR head at the time.
            try:
                build_commit = self.ghrepo.get_commit(build["commit"]["sha"])
                assert len(build_commit.parents) == 2
                return build_commit.parents[1].sha
            except (AssertionError, UnknownObjectException):
                log.info(
                    "Could not determine PR head commit for build; setting to 'UNK'"
                )
                return None
        else:
            raise AssertionError(f"Unhandled EventType: {event_type!r}")


class TravisJobLog(BuildLog):
    job: str
    job_id: int
    index: int

    @classmethod
    def from_job(
        cls,
        client: APIClient,
        build: Dict[str, Any],
        job: Dict[str, Any],
        index: int,
        commit: Optional[str],
        event_type: EventType,
    ) -> "TravisJobLog":
        created_at = isoparse(build["started_at"])
        event_id: str
        if event_type in (EventType.CRON, EventType.MANUAL):
            event_id = created_at.strftime("%Y%m%dT%H%M%S")
        elif event_type is EventType.PUSH:
            event_id = build["branch"]["name"]
        elif event_type is EventType.PULL_REQUEST:
            event_id = str(build["pull_request_number"])
        else:
            raise AssertionError(f"Unhandled EventType: {event_type!r}")
        return cls(
            client=client,
            created_at=created_at,
            event_type=event_type,
            event_id=event_id,
            build_commit=build["commit"]["sha"],
            commit=commit,
            number=int(build["number"]),
            job=removeprefix(job["number"], f"{build['number']}."),
            job_id=job["id"],
            status=job["state"],
            index=index,
        )

    def path_fields(self) -> Dict[str, Any]:
        fields = super().path_fields()
        fields.update(
            {
                "ci": "travis",
                "job": self.job,
                "job_index": str(self.index),
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
        self.client.download(f"/job/{self.job_id}/log.txt", path)
        return [path]
