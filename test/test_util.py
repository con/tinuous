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
