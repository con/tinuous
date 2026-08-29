from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock

from pytest_mock import MockerFixture

from tinuous.base import APIClient, EventType, GHWorkflowSpec
from tinuous.github import GitHubActions, Workflow, WorkflowRun, Repository


def make_workflow(wf_id: int, name: str, path: str) -> Workflow:
    """Helper to create a Workflow object."""
    now = datetime.now(timezone.utc)
    return Workflow(
        id=wf_id,
        name=name,
        path=path,
        state="active",
        created_at=now,
        updated_at=now,
    )


def make_run(
    run_id: int,
    run_number: int,
    head_sha: str,
    event: str,
    status: str,
    conclusion: str | None = None,
    pull_requests: list | None = None,
) -> WorkflowRun:
    """Helper to create a WorkflowRun object."""
    now = datetime.now(timezone.utc)
    return WorkflowRun(
        id=run_id,
        name="Test Run",
        head_branch="main",
        head_sha=head_sha,
        run_number=run_number,
        run_attempt=1,
        event=event,
        status=status,
        conclusion=conclusion,
        workflow_id=1,
        pull_requests=pull_requests or [],
        created_at=now,
        updated_at=now,
        logs_url=f"https://api.github.com/repos/test/test/actions/runs/{run_id}/logs",
        artifacts_url=f"https://api.github.com/repos/test/test/actions/runs/{run_id}/artifacts",  # noqa: E501
        workflow_url="https://api.github.com/repos/test/test/actions/workflows/1",
        repository=Repository(full_name="test/test"),
    )


class TestGitHubActionsSkipIncompletePR:
    """Test that PRs with incomplete workflows are skipped entirely."""

    def test_skip_pr_with_incomplete_workflow(
        self, mocker: MockerFixture
    ) -> None:
        """
        Test that when a PR has multiple workflows and one is still running,
        all workflows for that PR are skipped.
        """
        # Setup mock data
        wf1 = make_workflow(1, "Build", ".github/workflows/build.yaml")
        wf2 = make_workflow(2, "Test", ".github/workflows/test.yaml")

        # PR with sha "abc123" has one completed and one running workflow
        run1_completed = make_run(
            101, 1, "a" * 40, "pull_request", "completed", "success"
        )
        run2_running = make_run(102, 2, "a" * 40, "pull_request", "in_progress")

        # Create the GitHubActions instance with mocked methods
        gh = GitHubActions(
            repo="test/test",
            token="fake_token",
            since=datetime(2020, 1, 1, tzinfo=timezone.utc),
            workflow_spec=GHWorkflowSpec(),
        )

        # Create a helper function that returns new iterators each time
        def get_runs_mock(
            self_: Any, wf: Workflow, since: datetime  # noqa: U100
        ) -> Iterator[WorkflowRun]:
            if wf.id == 1:
                return iter([run1_completed])
            else:
                return iter([run2_running])

        # Mock the methods on the class
        mocker.patch.object(
            GitHubActions, "get_workflows", return_value=iter([wf1, wf2])
        )
        mocker.patch.object(GitHubActions, "get_runs", get_runs_mock)

        # Run get_build_assets for PR events
        assets = list(
            gh.get_build_assets(
                event_types=[EventType.PULL_REQUEST], logs=True, artifacts=False
            )
        )

        # Should return no assets because one workflow is incomplete
        assert len(assets) == 0

        # Check that both runs were registered as not processed
        assert len(gh.fetched) == 2
        assert all(not processed for _, processed in gh.fetched)

    def test_process_pr_when_all_workflows_complete(
        self, mocker: MockerFixture
    ) -> None:
        """
        Test that when all workflows for a PR are complete, all are processed.
        """
        wf1 = make_workflow(1, "Build", ".github/workflows/build.yaml")
        wf2 = make_workflow(2, "Test", ".github/workflows/test.yaml")

        # Both workflows completed for the same PR
        run1_completed = make_run(
            101, 1, "a" * 40, "pull_request", "completed", "success"
        )
        run2_completed = make_run(
            102, 2, "a" * 40, "pull_request", "completed", "success"
        )

        gh = GitHubActions(
            repo="test/test",
            token="fake_token",
            since=datetime(2020, 1, 1, tzinfo=timezone.utc),
            workflow_spec=GHWorkflowSpec(),
        )

        # Mock the client to avoid actual API calls
        mock_client = MagicMock(spec=APIClient)
        mocker.patch.object(GitHubActions, "client", new_callable=lambda: mock_client)

        def get_runs_mock(
            self_: Any, wf: Workflow, since: datetime  # noqa: U100
        ) -> Iterator[WorkflowRun]:
            if wf.id == 1:
                return iter([run1_completed])
            else:
                return iter([run2_completed])

        mocker.patch.object(
            GitHubActions, "get_workflows", return_value=iter([wf1, wf2])
        )
        mocker.patch.object(GitHubActions, "get_runs", get_runs_mock)
        mocker.patch.object(GitHubActions, "get_event_id", return_value="123")

        assets = list(
            gh.get_build_assets(
                event_types=[EventType.PULL_REQUEST], logs=True, artifacts=False
            )
        )

        # Should return 2 build logs (one for each workflow)
        assert len(assets) == 2

        # Check that both runs were registered as processed
        assert len(gh.fetched) == 2
        assert all(processed for _, processed in gh.fetched)

    def test_non_pr_events_not_grouped(self, mocker: MockerFixture) -> None:
        """
        Test that non-PR events (like push) are processed individually,
        not grouped by commit.
        """
        wf1 = make_workflow(1, "Build", ".github/workflows/build.yaml")
        wf2 = make_workflow(2, "Test", ".github/workflows/test.yaml")

        # Push event - completed
        run1_completed = make_run(
            101, 1, "a" * 40, "push", "completed", "success"
        )
        # Push event - still running (different workflow)
        run2_running = make_run(102, 2, "a" * 40, "push", "in_progress")

        gh = GitHubActions(
            repo="test/test",
            token="fake_token",
            since=datetime(2020, 1, 1, tzinfo=timezone.utc),
            workflow_spec=GHWorkflowSpec(),
        )

        mock_client = MagicMock(spec=APIClient)
        mocker.patch.object(GitHubActions, "client", new_callable=lambda: mock_client)

        def get_runs_mock(
            self_: Any, wf: Workflow, since: datetime  # noqa: U100
        ) -> Iterator[WorkflowRun]:
            if wf.id == 1:
                return iter([run1_completed])
            else:
                return iter([run2_running])

        mocker.patch.object(
            GitHubActions, "get_workflows", return_value=iter([wf1, wf2])
        )
        mocker.patch.object(GitHubActions, "get_runs", get_runs_mock)
        mocker.patch.object(GitHubActions, "get_event_id", return_value="main")

        assets = list(
            gh.get_build_assets(
                event_types=[EventType.PUSH], logs=True, artifacts=False
            )
        )

        # Should return 1 asset (only the completed push run)
        # The running push run is skipped individually
        assert len(assets) == 1

    def test_mixed_pr_and_push_events(self, mocker: MockerFixture) -> None:
        """
        Test that PR events are grouped while push events are not.
        """
        wf1 = make_workflow(1, "Build", ".github/workflows/build.yaml")

        # PR run - completed
        run1_pr_completed = make_run(
            101, 1, "a" * 40, "pull_request", "completed", "success"
        )
        # Push run - completed
        run2_push_completed = make_run(
            102, 2, "b" * 40, "push", "completed", "success"
        )

        gh = GitHubActions(
            repo="test/test",
            token="fake_token",
            since=datetime(2020, 1, 1, tzinfo=timezone.utc),
            workflow_spec=GHWorkflowSpec(),
        )

        mock_client = MagicMock(spec=APIClient)
        mocker.patch.object(GitHubActions, "client", new_callable=lambda: mock_client)

        mocker.patch.object(GitHubActions, "get_workflows", return_value=iter([wf1]))
        mocker.patch.object(
            GitHubActions,
            "get_runs",
            return_value=iter([run1_pr_completed, run2_push_completed]),
        )

        def get_event_id_mock(
            self_: Any, run: WorkflowRun, evt: EventType  # noqa: U100
        ) -> str:
            return "123" if evt == EventType.PULL_REQUEST else "main"

        mocker.patch.object(GitHubActions, "get_event_id", get_event_id_mock)

        assets = list(
            gh.get_build_assets(
                event_types=[EventType.PULL_REQUEST, EventType.PUSH],
                logs=True,
                artifacts=False,
            )
        )

        # Should return 2 assets (1 PR + 1 push)
        assert len(assets) == 2


class TestGitHubActionsSkipIncompleteCommit:
    """Test that commits with incomplete workflows are skipped."""

    def test_skip_commit_with_incomplete_workflow(
        self, mocker: MockerFixture
    ) -> None:
        """
        Test that when fetching for a specific commit, if any workflow is
        still running, all workflows are skipped.
        """
        wf1 = make_workflow(1, "Build", ".github/workflows/build.yaml")
        wf2 = make_workflow(2, "Test", ".github/workflows/test.yaml")

        # Use a valid 40-char SHA to avoid expand_committish being called
        commit_sha = "a" * 40
        run1_completed = make_run(
            101, 1, commit_sha, "pull_request", "completed", "success"
        )
        run2_running = make_run(102, 2, commit_sha, "pull_request", "in_progress")

        gh = GitHubActions(
            repo="test/test",
            token="fake_token",
            since=datetime(2020, 1, 1, tzinfo=timezone.utc),
            workflow_spec=GHWorkflowSpec(),
        )

        def get_runs_for_head_mock(
            self_: Any, wf: Workflow, sha: str  # noqa: U100
        ) -> Iterator[WorkflowRun]:
            if wf.id == 1:
                return iter([run1_completed])
            else:
                return iter([run2_running])

        mocker.patch.object(
            GitHubActions, "get_workflows", return_value=iter([wf1, wf2])
        )
        mocker.patch.object(GitHubActions, "get_runs_for_head", get_runs_for_head_mock)

        assets = list(
            gh.get_build_assets_for_commit(
                commit_sha,
                event_types=[EventType.PULL_REQUEST],
                logs=True,
                artifacts=False,
            )
        )

        # Should return no assets because one workflow is incomplete
        assert len(assets) == 0

    def test_process_commit_when_all_complete(
        self, mocker: MockerFixture
    ) -> None:
        """
        Test that when all workflows for a commit are complete,
        all are processed.
        """
        wf1 = make_workflow(1, "Build", ".github/workflows/build.yaml")
        wf2 = make_workflow(2, "Test", ".github/workflows/test.yaml")

        # Use a valid 40-char SHA to avoid expand_committish being called
        commit_sha = "a" * 40
        run1_completed = make_run(
            101, 1, commit_sha, "pull_request", "completed", "success"
        )
        run2_completed = make_run(
            102, 2, commit_sha, "pull_request", "completed", "success"
        )

        gh = GitHubActions(
            repo="test/test",
            token="fake_token",
            since=datetime(2020, 1, 1, tzinfo=timezone.utc),
            workflow_spec=GHWorkflowSpec(),
        )

        mock_client = MagicMock(spec=APIClient)
        mocker.patch.object(GitHubActions, "client", new_callable=lambda: mock_client)

        def get_runs_for_head_mock(
            self_: Any, wf: Workflow, sha: str  # noqa: U100
        ) -> Iterator[WorkflowRun]:
            if wf.id == 1:
                return iter([run1_completed])
            else:
                return iter([run2_completed])

        mocker.patch.object(
            GitHubActions, "get_workflows", return_value=iter([wf1, wf2])
        )
        mocker.patch.object(GitHubActions, "get_runs_for_head", get_runs_for_head_mock)
        mocker.patch.object(GitHubActions, "get_event_id", return_value="123")

        assets = list(
            gh.get_build_assets_for_commit(
                commit_sha,
                event_types=[EventType.PULL_REQUEST],
                logs=True,
                artifacts=False,
            )
        )

        # Should return 2 build logs
        assert len(assets) == 2
