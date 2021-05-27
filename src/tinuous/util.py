from typing import Dict, cast


def expand_template(
    template_str: str, fields: Dict[str, str], vars: Dict[str, str]
) -> str:
    expanded_vars: Dict[str, str] = {}
    for name, tmplt in vars.items():
        expanded_vars[name] = fstring(tmplt, **fields, **expanded_vars)
    return fstring(template_str, **fields, **expanded_vars)


def fstring(s: str, **kwargs: str) -> str:
    return cast(str, eval(f"f{s!r}", {}, kwargs))
