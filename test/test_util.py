import pytest
from tinuous.util import expand_template, parse_slice


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
