import pytest

from tinuous.base import WorkflowSpec


@pytest.mark.parametrize(
    "spec,path,r",
    [
        (
            WorkflowSpec(include=["build.yaml"], exclude=[], regex=False),
            ".github/workflows/build.yaml",
            True,
        ),
        (
            WorkflowSpec(include=["build.yaml"], exclude=[], regex=False),
            ".github/workflows/build.yml",
            False,
        ),
        (
            WorkflowSpec(include=[r"^build-*\.ya?ml$"], exclude=[], regex=False),
            ".github/workflows/build-foo.yml",
            False,
        ),
        (
            WorkflowSpec(include=[r"^build-.*\.ya?ml$"], exclude=[], regex=True),
            ".github/workflows/build-foo.yml",
            True,
        ),
        (
            WorkflowSpec(include=[r"^build-.*\.ya?ml$"], exclude=[], regex=True),
            ".github/workflows/build-foo.yaml",
            True,
        ),
        (
            WorkflowSpec(
                include=[r"^build-.*\.ya?ml$"],
                exclude=[r"^build-box\.yaml$"],
                regex=True,
            ),
            ".github/workflows/build-foo.yaml",
            True,
        ),
        (
            WorkflowSpec(
                include=[r"^build-.*\.ya?ml$"],
                exclude=[r"^build-box\.yaml$"],
                regex=True,
            ),
            ".github/workflows/build-box.yaml",
            False,
        ),
        (
            WorkflowSpec(include=["build.yaml", "test.yml"], exclude=[], regex=True),
            ".github/workflows/build.yaml",
            True,
        ),
        (
            WorkflowSpec(include=["build.yaml", "test.yml"], exclude=[], regex=True),
            ".github/workflows/test.yml",
            True,
        ),
    ],
)
def test_workflowspec_match(spec: WorkflowSpec, path: str, r: bool) -> None:
    assert spec.match(path) is r
