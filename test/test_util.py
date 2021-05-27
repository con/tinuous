from tinuous.util import expand_template


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
