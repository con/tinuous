from functools import cached_property
from hashlib import sha1
import os
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from dateutil.parser import isoparse

from .base import APIClient, BuildAsset, BuildLog, CISystem, EventType
from .util import log, removeprefix, sanitize_pathname


class Appveyor(CISystem):
    accountName: str
    projectSlug: Optional[str]

    @staticmethod
    def get_auth_tokens() -> Dict[str, str]:
        token = os.environ.get("APPVEYOR_TOKEN")
        if not token:
            raise RuntimeError(
                "Appveyor API key not set.  Set via APPVEYOR_TOKEN environment"
                " variable."
            )
        return {"appveyor": token}

    @property
    def repo_slug(self) -> str:
        if self.projectSlug is None:
            return self.repo.split("/")[1]
        else:
            return self.projectSlug

    @cached_property
    def client(self) -> APIClient:
        return APIClient(
            "https://ci.appveyor.com",
            {"Authorization": f"Bearer {self.token}"},
        )

    def get_builds(self) -> Iterator[dict]:
        params = {"recordsNumber": 20}
        while True:
            data = self.client.get(
                f"/api/projects/{self.accountName}/{self.repo_slug}/history",
                params=params,
            ).json()
            if data.get("builds"):
                yield from data["builds"]
                params["startBuildId"] = data["builds"][-1]["buildId"]
            else:
                break

    def get_build_assets(
        self, event_types: List[EventType], logs: bool, artifacts: bool  # noqa: U100
    ) -> Iterator["BuildAsset"]:
        if not logs:
            log.debug("No assets requested for Appveyor builds")
            return
        log.info("Fetching builds newer than %s", self.since)
        if self.until is not None:
            log.info("Skipping builds newer than %s", self.until)
        for build in self.get_builds():
            if build.get("pullRequestId"):
                run_event = EventType.PULL_REQUEST
            else:
                run_event = EventType.PUSH
            ts = isoparse(build["created"])
            if ts <= self.since:
                break
            elif self.until is not None and ts > self.until:
                log.info("Build %s is too new; skipping", build["buildNumber"])
            elif build.get("finished") is None:
                log.info("Build %s not completed; skipping", build["buildNumber"])
                self.register_build(ts, False)
            else:
                log.info("Found build %s", build["buildNumber"])
                self.register_build(ts, True)
                if run_event in event_types:
                    for i, job in enumerate(
                        self.client.get(
                            f"/api/projects/{self.accountName}/{self.repo_slug}"
                            f"/build/{build['version']}"
                        ).json()["build"]["jobs"],
                        start=1,
                    ):
                        yield AppveyorJobLog.from_job(self.client, build, job, i)
                else:
                    log.info("Event type is %r; skipping", run_event.value)


class AppveyorJobLog(BuildLog):
    job: str
    index: int
    envvars: str

    @classmethod
    def from_job(
        cls,
        client: APIClient,
        build: Dict[str, Any],
        job: Dict[str, Any],
        index: int,
    ) -> "AppveyorJobLog":
        created_at = isoparse(build["created"])
        if build.get("pullRequestId"):
            event = EventType.PULL_REQUEST
            event_id = build["pullRequestId"]
            commit = build["pullRequestHeadCommitId"]
        else:
            event = EventType.PUSH
            event_id = build["branch"]
            commit = build["commitId"]
        return cls(
            client=client,
            created_at=created_at,
            event_type=event,
            event_id=event_id,
            build_commit=build["commitId"],
            commit=commit,
            number=build["buildNumber"],
            job=job["jobId"],
            status=job["status"],
            index=index,
            envvars=removeprefix(job["name"], "Environment: "),
        )

    def path_fields(self) -> Dict[str, Any]:
        fields = super().path_fields()
        fields.update(
            {
                "ci": "appveyor",
                "job": self.job,
                "job_index": str(self.index),
                "job_env": sanitize_pathname(self.envvars),
                "job_env_hash": sha1(self.envvars.encode("utf-8")).hexdigest(),
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
        self.client.download(f"/api/buildjobs/{self.job}/log", path)
        return [path]
