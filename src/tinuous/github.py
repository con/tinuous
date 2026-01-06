from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone
from functools import cached_property
import json
from pathlib import Path
import re
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from pydantic import BaseModel, Field
import requests

from .base import (
    APIClient,
    Artifact,
    BuildAsset,
    BuildLog,
    CISystem,
    EventType,
    GHWorkflowSpec,
)
from .util import expand_template, get_github_token, iterfiles, log, sanitize_pathname


class GitHubActions(CISystem):
    workflow_spec: GHWorkflowSpec
    hash2pr: Dict[str, str] = Field(default_factory=dict)

    @staticmethod
    def get_auth_tokens() -> dict[str, str]:
        return {"github": get_github_token()}

    @cached_property
    def client(self) -> APIClient:
        return APIClient(
            "https://api.github.com",
            {
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            is_github=True,
        )

    def paginate(
        self, path: str, params: Optional[dict[str, str]] = None
    ) -> Iterator[dict]:
        while path is not None:
            r = self.client.get(path, params=params)
            data = r.json()
            if isinstance(data, list):
                yield from data
            else:
                assert isinstance(data, dict)
                itemses = [v for k, v in data.items() if k != "total_count"]
                if len(itemses) != 1:
                    raise ValueError(
                        f"Unique non-count key not found in {path} response"
                    )
                yield from itemses[0]
            path = r.links.get("next", {}).get("url")
            params = None

    def get_workflows(self) -> Iterator[Workflow]:
        for item in self.paginate(f"/repos/{self.repo}/actions/workflows"):
            wf = Workflow.model_validate(item)
            if self.workflow_spec.match(wf.path):
                yield wf

    def get_runs(self, wf: Workflow, since: datetime) -> Iterator[WorkflowRun]:
        for item in self.paginate(f"/repos/{self.repo}/actions/workflows/{wf.id}/runs"):
            r = WorkflowRun.model_validate(item)
            if r.created_at <= since:
                break
            yield r

    def get_runs_for_head(self, wf: Workflow, head_sha: str) -> Iterator[WorkflowRun]:
        for item in self.paginate(
            f"/repos/{self.repo}/actions/workflows/{wf.id}/runs",
            params={"head_sha": head_sha},
        ):
            yield WorkflowRun.model_validate(item)

    def expand_committish(self, committish: str) -> str:
        try:
            r = self.client.get(
                f"/repos/{self.repo}/commits/{quote(committish)}",
                headers={"Accept": "application/vnd.github.sha"},
            )
        except requests.HTTPError:
            raise ValueError(f"Failed to expand committish {committish}")
        else:
            return r.text.strip()

    def get_build_assets(
        self, event_types: list[EventType], logs: bool, artifacts: bool
    ) -> Iterator[BuildAsset]:
        if not logs and not artifacts:
            log.debug("No assets requested for GitHub Actions runs")
            return
        log.info("Fetching runs newer than %s", self.since)
        if self.until is not None:
            log.info("Skipping runs newer than %s", self.until)
        for wf in self.get_workflows():
            log.info("Fetching runs for workflow %s (%s)", wf.path, wf.name)
            for run in self.get_runs(wf, self.since):
                run_event = EventType.from_gh_event(run.event)
                ts = run.created_at
                if self.until is not None and ts > self.until:
                    log.info("Run %s is too new; skipping", run.run_number)
                elif run.status != "completed":
                    log.info("Run %s not completed; skipping", run.run_number)
                    self.register_build(ts, False)
                else:
                    log.info("Found run %s", run.run_number)
                    self.register_build(ts, True)
                    if run_event in event_types:
                        event_id = self.get_event_id(run, run_event)
                        if logs:
                            yield GHABuildLog.from_workflow_run(
                                self.client, wf, run, run_event, event_id
                            )
                        if artifacts:
                            for name, download_url in self.get_artifacts(run):
                                yield GHAArtifact.from_workflow_run(
                                    self.client,
                                    wf,
                                    run,
                                    run_event,
                                    event_id,
                                    name,
                                    download_url,
                                )
                    else:
                        log.info("Event type is %r; skipping", run.event)

    def get_build_assets_for_commit(
        self, committish: str, event_types: list[EventType], logs: bool, artifacts: bool
    ) -> Iterator[BuildAsset]:
        if not logs and not artifacts:
            log.debug("No assets requested for GitHub Actions runs")
            return
        if not re.fullmatch(r"[0-9A-Fa-f]{40}", committish):
            committish2 = self.expand_committish(committish)
            log.info("Expanded committish %r to full sha %s", committish, committish2)
            committish = committish2
        log.info("Fetching runs for commit %s", committish)
        for wf in self.get_workflows():
            log.info("Fetching runs for workflow %s (%s)", wf.path, wf.name)
            for run in self.get_runs_for_head(wf, committish):
                if run.status != "completed":
                    log.info("Run %s not completed; skipping", run.run_number)
                    continue
                log.info("Found run %s", run.run_number)
                run_event = EventType.from_gh_event(run.event)
                if run_event in event_types:
                    event_id = self.get_event_id(run, run_event)
                    if logs:
                        yield GHABuildLog.from_workflow_run(
                            self.client, wf, run, run_event, event_id
                        )
                    if artifacts:
                        for name, download_url in self.get_artifacts(run):
                            yield GHAArtifact.from_workflow_run(
                                self.client,
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
        if event_type in (EventType.CRON, EventType.MANUAL):
            return run.created_at.strftime("%Y%m%dT%H%M%S")
        elif event_type is EventType.PUSH:
            assert run.head_branch is not None
            return run.head_branch
        elif event_type is EventType.PULL_REQUEST:
            if run.pull_requests:
                return str(run.pull_requests[0].number)
            elif run.head_sha in self.hash2pr:
                return self.hash2pr[run.head_sha]
            else:
                r = self.client.get(
                    f"/repos/{self.repo}/commits/{run.head_sha}/pulls",
                    headers={"Accept": "application/vnd.github.groot-preview+json"},
                )
                if data := r.json():
                    pr = str(data[0]["number"])
                else:
                    # The above endpoint ignores PRs made from forks, so we
                    # have to fall back to performing an issue search to fill
                    # those in.  This should hopefully be used sparingly, as
                    # there's a 30 searches per hour rate limit.
                    if hits := self.client.get(
                        "/search/issues",
                        params={
                            "q": (
                                f"repo:{run.repository.full_name} is:pr"
                                f" {run.head_sha}"
                            ),
                            "sort": "created",
                            "order": "asc",
                        },
                    ).json()["items"]:
                        pr = str(hits[0]["number"])
                    else:
                        pr = "UNK"
                self.hash2pr[run.head_sha] = pr
                return pr
        else:
            raise AssertionError(f"Unhandled EventType: {event_type!r}")

    def get_artifacts(self, run: WorkflowRun) -> Iterator[tuple[str, str]]:
        """Yields each artifact as a (name, download_url) pair"""
        for artifact in self.paginate(run.artifacts_url):
            if not artifact["expired"]:
                yield (artifact["name"], artifact["archive_download_url"])

    def get_releases(self) -> Iterator[Release]:
        for item in self.paginate(f"/repos/{self.repo}/releases"):
            yield Release.model_validate(item)

    def get_release_assets(self) -> Iterator[GHReleaseAsset]:
        log.info("Fetching releases newer than %s", self.since)
        if self.until is not None:
            log.info("Skipping releases newer than %s", self.until)
        for rel in self.get_releases():
            if rel.draft:
                log.info("Release %s is draft; skipping", rel.tag_name)
                continue
            if rel.prerelease:
                log.info("Release %s is prerelease; skipping", rel.tag_name)
                continue
            ts = rel.published_at
            assert ts is not None
            if ts <= self.since or (self.until is not None and ts > self.until):
                continue
            self.register_build(ts, True)  # TODO: Set to False for drafts?
            log.info("Found release %s", rel.tag_name)
            r = self.client.get(f"/repos/{self.repo}/git/refs/tags/{rel.tag_name}")
            tagobj = r.json()["object"]
            if tagobj["type"] == "commit":
                commit = tagobj["sha"]
            elif tagobj["type"] == "tag":
                r = self.client.get(tagobj["url"])
                commit = r.json()["object"]["sha"]
            else:
                raise RuntimeError(
                    f"Unexpected type for tag {rel.tag_name}: {tagobj['type']!r}"
                )
            for asset in rel.assets:
                yield GHReleaseAsset(
                    client=self.client,
                    published_at=ts,
                    tag_name=rel.tag_name,
                    commit=commit,
                    name=asset.name,
                    download_url=asset.browser_download_url,
                )

    def get_packages(self) -> Iterator[Package]:
        # First get the owner type (user or org) from the repo name
        owner = self.repo.split("/")[0]
        # Try organization packages first, fall back to user packages
        for endpoint in [
            f"/orgs/{owner}/packages",
            f"/users/{owner}/packages",
        ]:
            try:
                params = {"package_type": "container"}
                for item in self.paginate(endpoint, params=params):
                    yield Package.model_validate(item)
                return  # Successfully fetched packages
            except requests.HTTPError as e:
                if e.response is not None and e.response.status_code == 404:
                    continue  # Try next endpoint
                raise

    def get_package_versions(self, package: Package) -> Iterator[PackageVersion]:
        owner = self.repo.split("/")[0]
        # Try organization packages first, fall back to user packages
        pkg_name = quote(package.name, safe="")
        for endpoint_base in [
            f"/orgs/{owner}/packages/container/{pkg_name}/versions",
            f"/users/{owner}/packages/container/{pkg_name}/versions",
        ]:
            try:
                for item in self.paginate(endpoint_base):
                    yield PackageVersion.model_validate(item)
                return  # Successfully fetched versions
            except requests.HTTPError as e:
                if e.response is not None and e.response.status_code == 404:
                    continue  # Try next endpoint
                raise

    def get_package_assets(self) -> Iterator[GHPackageAsset]:
        log.info("Fetching packages newer than %s", self.since)
        if self.until is not None:
            log.info("Skipping packages newer than %s", self.until)
        for pkg in self.get_packages():
            log.info("Found package %s", pkg.name)
            for version in self.get_package_versions(pkg):
                ts = version.updated_at
                if ts <= self.since or (self.until is not None and ts > self.until):
                    continue
                self.register_build(ts, True)
                tags = version.metadata.container.tags
                tags_str = ", ".join(tags) if tags else "(no tags)"
                log.info(
                    "Found package version %s (tags: %s) for %s",
                    version.name,
                    tags_str,
                    pkg.name,
                )
                yield GHPackageAsset(
                    client=self.client,
                    updated_at=ts,
                    package_name=pkg.name,
                    package_type=pkg.package_type,
                    version_id=version.id,
                    version_name=version.name,
                    tags=tags,
                    url=version.url,
                    html_url=version.html_url,
                    description=version.description,
                )


class GHAAsset(BuildAsset):
    workflow_name: str
    workflow_file: str
    run_id: int

    def path_fields(self) -> dict[str, Any]:
        fields = super().path_fields()
        fields.update(
            {
                "ci": "github",
                "wf_name": sanitize_pathname(self.workflow_name),
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
        client: APIClient,
        workflow: Workflow,
        run: WorkflowRun,
        event_type: EventType,
        event_id: str,
    ) -> GHABuildLog:
        return cls(
            client=client,
            logs_url=run.logs_url,
            created_at=run.created_at,
            event_type=event_type,
            event_id=event_id,
            build_commit=run.head_sha,
            commit=run.head_sha,
            workflow_name=workflow.name,
            workflow_file=workflow.path.split("/")[-1],
            number=run.run_number,
            run_id=run.id,
            status=run.conclusion,
        )

    def download(self, path: Path) -> list[Path]:
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
        try:
            self.client.download_zipfile(self.logs_url, path)
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code in (404, 410):
                # 404 can happen when a workflow failed to run due to, say, a
                # syntax error.  410 happens when the logs have expired.
                log.error(
                    "Request for logs returned %d; skipping", e.response.status_code
                )
                return []
            else:
                raise
        return list(path.rglob("*.txt"))


class GHAArtifact(GHAAsset, Artifact):
    name: str
    download_url: str

    @classmethod
    def from_workflow_run(
        cls,
        client: APIClient,
        workflow: Workflow,
        run: WorkflowRun,
        event_type: EventType,
        event_id: str,
        name: str,
        download_url: str,
    ) -> GHAArtifact:
        return cls(
            client=client,
            created_at=run.created_at,
            event_type=event_type,
            event_id=event_id,
            build_commit=run.head_sha,
            commit=run.head_sha,
            workflow_name=workflow.name,
            workflow_file=workflow.path.split("/")[-1],
            number=run.run_number,
            run_id=run.id,
            status=run.conclusion,
            name=name,
            download_url=download_url,
        )

    def download(self, path: Path) -> list[Path]:
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
        self.client.download_zipfile(self.download_url, target_dir)
        return list(iterfiles(target_dir))


# The `arbitrary_types_allowed` is for APIClient
class GHReleaseAsset(BaseModel, arbitrary_types_allowed=True):
    client: APIClient
    published_at: datetime
    tag_name: str
    commit: str
    name: str
    download_url: str

    def path_fields(self) -> dict[str, Any]:
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
            "release_tag": self.tag_name,
            "build_commit": self.commit,
            "commit": self.commit,
        }

    def expand_path(self, path_template: str, variables: dict[str, str]) -> str:
        return expand_template(path_template, self.path_fields(), variables)

    def download(self, path: Path) -> list[Path]:
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
        self.client.download(self.download_url, target, headers={"Accept": "*/*"})
        return [target]


class Workflow(BaseModel):
    id: int
    name: str
    path: str
    state: str
    created_at: datetime
    updated_at: datetime


class PullRequest(BaseModel):
    number: int


class Repository(BaseModel):
    full_name: str


class WorkflowRun(BaseModel):
    id: int
    name: Optional[str] = None
    head_branch: Optional[str] = None
    head_sha: str
    run_number: int
    run_attempt: Optional[int] = None
    event: str
    status: Optional[str] = None
    conclusion: Optional[str] = None
    workflow_id: int
    pull_requests: List[PullRequest]
    created_at: datetime
    updated_at: datetime
    logs_url: str
    artifacts_url: str
    workflow_url: str
    repository: Repository


class ReleaseAsset(BaseModel):
    browser_download_url: str
    name: str


class Release(BaseModel):
    tag_name: str
    draft: bool
    prerelease: bool
    created_at: datetime
    published_at: Optional[datetime] = None
    assets: List[ReleaseAsset]


class ContainerMetadata(BaseModel):
    tags: List[str]


class PackageMetadata(BaseModel):
    container: ContainerMetadata


class PackageVersion(BaseModel):
    id: int
    name: str
    url: Optional[str] = None
    package_html_url: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    html_url: Optional[str] = None
    metadata: PackageMetadata
    # Additional fields that may be present
    description: Optional[str] = None


class Package(BaseModel):
    id: int
    name: str
    package_type: str
    created_at: datetime
    updated_at: datetime


# The `arbitrary_types_allowed` is for APIClient
class GHPackageAsset(BaseModel, arbitrary_types_allowed=True):
    client: APIClient
    updated_at: datetime
    package_name: str
    package_type: str
    version_id: int
    version_name: str
    tags: List[str]
    url: Optional[str] = None
    html_url: Optional[str] = None
    description: Optional[str] = None

    def path_fields(self) -> dict[str, Any]:
        utc_date = self.updated_at.astimezone(timezone.utc)
        # Use the first tag as primary tag, or version_name if no tags
        primary_tag = self.tags[0] if self.tags else self.version_name
        return {
            "timestamp": utc_date,
            "timestamp_local": self.updated_at.astimezone(),
            "year": utc_date.strftime("%Y"),
            "month": utc_date.strftime("%m"),
            "day": utc_date.strftime("%d"),
            "hour": utc_date.strftime("%H"),
            "minute": utc_date.strftime("%M"),
            "second": utc_date.strftime("%S"),
            "ci": "github",
            "type": "package",
            "package_name": sanitize_pathname(self.package_name),
            "package_type": self.package_type,
            "version_id": str(self.version_id),
            "version_name": sanitize_pathname(self.version_name),
            "tag": sanitize_pathname(primary_tag),
            "tags": ",".join(sanitize_pathname(t) for t in self.tags),
        }

    def expand_path(self, path_template: str, variables: dict[str, str]) -> str:
        return expand_template(path_template, self.path_fields(), variables)

    def download(self, path: Path) -> list[Path]:
        # For packages (containers), we download metadata and manifest
        path.mkdir(parents=True, exist_ok=True)

        # Save basic metadata
        metadata_file = path / "metadata.json"
        manifest_file = path / "manifest.json"

        if metadata_file.exists() and manifest_file.exists():
            log.info(
                "Package %s version %s already downloaded to %s; skipping",
                self.package_name,
                self.version_name,
                path,
            )
            return []

        downloaded_files = []

        # Save basic metadata
        if not metadata_file.exists():
            log.info(
                "Saving metadata for package %s version %s to %s",
                self.package_name,
                self.version_name,
                metadata_file,
            )
            metadata = {
                "package_name": self.package_name,
                "package_type": self.package_type,
                "version_id": self.version_id,
                "version_name": self.version_name,
                "tags": self.tags,
                "updated_at": self.updated_at.isoformat(),
                "url": self.url,
                "html_url": self.html_url,
                "description": self.description,
            }
            with metadata_file.open("w", encoding="utf-8") as fp:
                json.dump(metadata, fp, indent=2)
            downloaded_files.append(metadata_file)

        # Download container manifest for GHCR
        if self.package_type == "container" and not manifest_file.exists():
            try:
                log.info(
                    "Downloading manifest for package %s version %s",
                    self.package_name,
                    self.version_name,
                )
                manifest = self._download_container_manifest()
                if manifest:
                    with manifest_file.open("w", encoding="utf-8") as fp:
                        json.dump(manifest, fp, indent=2)
                    downloaded_files.append(manifest_file)
                    log.info("Saved manifest to %s", manifest_file)
            except Exception as e:
                log.warning(
                    "Failed to download manifest for %s: %s",
                    self.package_name,
                    str(e),
                )

        return downloaded_files

    def _download_container_manifest(self) -> Optional[dict[str, Any]]:
        """Download the OCI/Docker manifest for a container package."""
        # GHCR registry URL pattern: ghcr.io/<owner>/<package>:<tag>
        # or @<digest>

        # Extract owner from package name if it contains a slash
        # Otherwise use the repo owner from the client
        owner_val = self.package_name.split("/")[0]
        owner = owner_val if "/" in self.package_name else None

        # For GitHub Container Registry, use the registry API
        # Manifest: ghcr.io/v2/<owner>/<image>/manifests/<tag_or_digest>

        # Get the first tag or use the version_name (digest)
        reference = self.tags[0] if self.tags else self.version_name

        # Construct the manifest URL for GHCR
        # Extract the owner from the API URL if available
        if self.url:
            # URL format: https://api.github.com/orgs/{owner}/packages/
            #   container/{package}/versions/{id}
            # or https://api.github.com/users/{owner}/packages/
            #   container/{package}/versions/{id}
            match = re.search(r'/(orgs|users)/([^/]+)/', self.url)
            if match:
                owner = match.group(2)

        if not owner:
            log.warning("Could not determine owner for manifest download")
            return None

        # Construct the GHCR manifest URL
        base_url = f"https://ghcr.io/v2/{owner}/{self.package_name}"
        manifest_url = f"{base_url}/manifests/{reference}"

        try:
            # Request the manifest with Accept header for OCI manifest
            accept_header = (
                "application/vnd.oci.image.manifest.v1+json, "
                "application/vnd.docker.distribution.manifest.v2+json, "
                "application/vnd.docker.distribution.manifest.list.v2+json"
            )
            headers = {"Accept": accept_header}

            # Use the GitHub token for authentication with GHCR
            # GHCR uses the GitHub token as a bearer token
            r = self.client.get(manifest_url, headers=headers)
            manifest: dict[str, Any] = r.json()
            return manifest
        except Exception as e:
            log.debug(
                "Failed to fetch manifest from %s: %s",
                manifest_url,
                str(e),
            )
            return None
