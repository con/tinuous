from typing import Dict


def expand_template(
    template_str: str, fields: Dict[str, str], vars: Dict[str, str]
) -> str:
    expanded_vars: Dict[str, str] = {}
    for name, tmplt in vars.items():
        expanded_vars[name] = tmplt.format(**fields, **expanded_vars)
    return template_str.format(**fields, **expanded_vars)
