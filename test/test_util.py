from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Dict

import pytest
from pytest_mock import MockerFixture

from tinuous.util import (
    LazySlicingFormatter,
    expand_template,
    parse_slice,
    removeprefix,
    sanitize_pathname,
)


def test_expand_template() -> None:
    assert (
        expand_template(
            "{foo}/{cleesh}",
            {"foo": "FOO", "bar": "BAR", "baz": "BAZ", "quux": "QUUX"},
            {"gnusto": "{bar}-{baz}", "cleesh": "{gnusto}.{quux}"},
        )
        == "FOO/BAR-BAZ.QUUX"
    )


def test_expand_template_sliced() -> None:
    assert (
        expand_template(
            "{commit[:7]}/{cleesh}",
            {
                "commit": "123456789012345678901234567890",
                "description": "A test commit",
            },
            {"cleesh": "{description[:6]}"},
        )
        == "1234567/A test"
    )


def test_expand_template_unused_bad() -> None:
    assert (
        expand_template(
            "{cleesh}",
            {"description": "A test commit"},
            {"bad": "{undefined}", "cleesh": "{description[:6]}"},
        )
        == "A test"
    )


def test_expand_template_datetime_format() -> None:
    assert (
        expand_template(
            "{when:%Y-%b-%d}",
            {"when": datetime(2021, 6, 14, 14, 44, 25, tzinfo=timezone.utc)},
            {},
        )
        == "2021-Jun-14"
    )


@pytest.mark.parametrize(
    "s,sl",
    [
        (":", slice(None)),
        ("::", slice(None)),
        ("23:", slice(23, None)),
        ("23::", slice(23, None)),
        (":42", slice(42)),
        (":42:", slice(42)),
        ("23:42", slice(23, 42)),
        ("23:42:", slice(23, 42)),
        ("::5", slice(None, None, 5)),
        ("23::5", slice(23, None, 5)),
        (":42:5", slice(None, 42, 5)),
        ("23:42:5", slice(23, 42, 5)),
        ("-23:-42:-5", slice(-23, -42, -5)),
    ],
)
def test_parse_slice(s: str, sl: slice) -> None:
    assert parse_slice(s) == sl


@pytest.mark.parametrize(
    "fmt,args,kwargs,result",
    [
        ("{0}", ["foo"], {}, "foo"),
        (
            "{foo.bar.baz}",
            [],
            {"foo": SimpleNamespace(bar=SimpleNamespace(baz="quux"))},
            "quux",
        ),
        ("{foo[1][2]}", [], {"foo": ["abc", "def", "ghi"]}, "f"),
        ("{foo[bar][baz]}", [], {"foo": {"bar": {"baz": "quux"}}}, "quux"),
    ],
)
def test_lazy_slicing_formatter_basics(
    fmt: str, args: list, kwargs: Dict[str, Any], result: str
) -> None:
    assert LazySlicingFormatter({}).format(fmt, *args, **kwargs) == result


def test_lazy_slicing_formatter_undef_key() -> None:
    with pytest.raises(KeyError):
        LazySlicingFormatter({}).format("{foo}", bar=42)


def test_lazy_slicing_formatter_var_reuse(mocker: MockerFixture) -> None:
    fmter = LazySlicingFormatter({"foo": "bar"})
    spy = mocker.spy(fmter, "format")
    assert fmter.format("-{foo}-{foo}-") == "-bar-bar-"
    assert spy.call_args_list == [mocker.call("-{foo}-{foo}-"), mocker.call("bar")]


@pytest.mark.parametrize(
    "s,prefix,result",
    [
        ("foobar", "foo", "bar"),
        ("foobar", "bar", "foobar"),
        ("foobar", "", "foobar"),
        ("foobar", "foobar", ""),
        ("foobar", "foobarx", "foobar"),
        ("foobar", "xfoobar", "foobar"),
    ],
)
def test_removeprefix(s: str, prefix: str, result: str) -> None:
    assert removeprefix(s, prefix) == result


@pytest.mark.parametrize(
    "s1,s2",
    [
        ("\\x20", "%5cx20"),
        ("foo/bar", "foo%2fbar"),
        ("<angle>", "%3cangle%3e"),
        ("foo:bar", "foo%3abar"),
        ("foo|bar", "foo%7cbar"),
        ('"foo"', "%22foo%22"),
        ("foo?", "foo%3f"),
        ("foo*bar", "foo%2abar"),
        ("foo%20bar", "foo%2520bar"),
        ("foo\0bar", "foo%00bar"),
    ],
)
def test_sanitize_pathname(s1: str, s2: str) -> None:
    assert sanitize_pathname(s1) == s2
