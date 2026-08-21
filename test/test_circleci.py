from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from tinuous.base import WorkflowSpec
from tinuous.circleci import CircleCI


class FakeResponse:
    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data

    def json(self) -> dict[str, Any]:
        return self.data


class FakeClient:
    """Serves canned responses and records the params of each request."""

    def __init__(self, pages: list[dict[str, Any]]) -> None:
        self.pages = pages
        self.requests: list[tuple[str, dict[str, str] | None]] = []

    def get(self, path: str, params: dict[str, str] | None = None) -> FakeResponse:
        self.requests.append((path, None if params is None else dict(params)))
        return FakeResponse(self.pages[len(self.requests) - 1])


def paginate(pages: list[dict[str, Any]]) -> tuple[list[dict], FakeClient]:
    ci = CircleCI(
        repo="con/tinuous",
        token="hunter2",
        since=datetime(2026, 1, 1, tzinfo=timezone.utc),
        workflow_spec=WorkflowSpec(include=[], exclude=[], regex=False),
    )
    client = FakeClient(pages)
    # Pre-seed the `client` cached_property so no real requests are made:
    ci.__dict__["client"] = client
    return list(ci.paginate("/v2/some/path")), client


def test_paginate_stops_on_null_token() -> None:
    items, client = paginate([{"items": [{"n": 1}], "next_page_token": None}])
    assert items == [{"n": 1}]
    assert client.requests == [("/v2/some/path", None)]


def test_paginate_stops_on_missing_token() -> None:
    # The API is documented as always returning "next_page_token", but it has
    # been observed to omit the field entirely.
    items, client = paginate([{"items": [{"n": 1}]}])
    assert items == [{"n": 1}]
    assert client.requests == [("/v2/some/path", None)]


def test_paginate_follows_token() -> None:
    items, client = paginate(
        [
            {"items": [{"n": 1}], "next_page_token": "tok1"},
            {"items": [{"n": 2}]},
        ]
    )
    assert items == [{"n": 1}, {"n": 2}]
    assert client.requests == [
        ("/v2/some/path", None),
        ("/v2/some/path", {"page-token": "tok1"}),
    ]
