from collections import deque
from datetime import datetime, timezone
import logging
from pathlib import Path
from typing import Dict, Iterator, cast

import requests


log = logging.getLogger("tinuous")


def ensure_aware(dt: datetime) -> datetime:
    # Pygithub returns naïve datetimes for timestamps with a "Z" suffix.  Until
    # that's fixed <https://github.com/PyGithub/PyGithub/pull/1831>, we need to
    # make such datetimes timezone-aware manually.
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def removeprefix(s: str, prefix: str) -> str:
    n = len(prefix)
    return s[n:] if s[:n] == prefix else s


def stream_to_file(r: requests.Response, p: Path) -> None:
    try:
        with p.open("wb") as fp:
            for chunk in r.iter_content(chunk_size=8192):
                fp.write(chunk)
    except BaseException:
        p.unlink(missing_ok=True)
        raise


def iterfiles(dirpath: Path) -> Iterator[Path]:
    dirs = deque([dirpath])
    while dirs:
        d = dirs.popleft()
        for p in d.iterdir():
            if p.is_dir():
                dirs.append(p)
            else:
                yield p


def expand_template(
    template_str: str, fields: Dict[str, str], vars: Dict[str, str]
) -> str:
    expanded_vars: Dict[str, str] = {}
    for name, tmplt in vars.items():
        expanded_vars[name] = fstring(tmplt, **fields, **expanded_vars)
    return fstring(template_str, **fields, **expanded_vars)


def fstring(s: str, **kwargs: str) -> str:
    return cast(str, eval(f"f{s!r}", {}, kwargs))
