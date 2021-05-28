from datetime import datetime
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Match, Optional, Pattern

import click
from click_loglevel import LogLevel
from dotenv import load_dotenv
from in_place import InPlace
from yaml import safe_load

from .base import Artifact, BuildLog
from .config import Config, GitHubConfig
from .github import GitHubActions
from .util import log


@click.group()
@click.option(
    "-c",
    "--config",
    type=click.Path(exists=True, dir_okay=False),
    default="config.yml",
    help="Read configuration from the given file",
    show_default=True,
)
@click.option(
    "-E",
    "--env",
    type=click.Path(exists=True, dir_okay=False),
    help="Load environment variables from given .env file",
)
@click.option(
    "-l",
    "--log-level",
    type=LogLevel(),
    default="INFO",
    help="Set logging level",
    show_default=True,
)
@click.pass_context
def main(ctx: click.Context, config: str, log_level: int, env: Optional[str]) -> None:
    """ Download build logs from GitHub Actions, Travis, and Appveyor """
    load_dotenv(env)
    logging.basicConfig(
        format="%(asctime)s [%(levelname)-8s] %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
        level=log_level,
    )
    with open(config) as fp:
        ctx.obj = Config.parse_obj(safe_load(fp))


@main.command()
@click.option(
    "--sanitize-secrets",
    is_flag=True,
    help="Sanitize strings matching secret patterns",
)
@click.option(
    "-S",
    "--state",
    type=click.Path(dir_okay=False, writable=True),
    default=".dlstate.json",
    help="Store program state in the given file",
    show_default=True,
)
@click.pass_obj
def fetch(cfg: Config, state: str, sanitize_secrets: bool) -> None:
    """ Download logs """
    if sanitize_secrets and not cfg.secrets:
        log.warning("--sanitize-secrets set but no secrets given in configuration")
    since_stamps: Dict[str, str]
    try:
        with open(state) as fp:
            since_stamps = json.load(fp)
    except FileNotFoundError:
        since_stamps = {}
    # Fetch tokens early in order to catch failures early:
    tokens: Dict[str, str] = {}
    for name, cicfg in cfg.ci.items():
        tokens[name] = cicfg.get_auth_token()
    if cfg.datalad.enabled:
        try:
            from datalad.api import Dataset
        except ImportError:
            raise click.UsageError("datalad.enabled set, but datalad is not installed")
        ds = Dataset(os.curdir)
        if not ds.is_installed():
            ds.create(force=True, cfg_proc=cfg.datalad.cfg_proc)
    logs_added = 0
    artifacts_added = 0
    relassets_added = 0
    for name, cicfg in cfg.ci.items():
        get_artifacts = getattr(cicfg, "artifacts_path", None) is not None
        log.info("Fetching resources from %s", name)
        try:
            since = datetime.fromisoformat(since_stamps[name])
        except KeyError:
            since = cfg.since
        ci = cicfg.get_system(repo=cfg.repo, since=since, token=tokens[name])
        for obj in ci.get_build_assets(cfg.types, artifacts=get_artifacts):
            if isinstance(obj, BuildLog):
                path = obj.expand_path(cicfg.path, cfg.vars)
            elif isinstance(obj, Artifact):
                assert get_artifacts
                path = obj.expand_path(
                    cicfg.artifacts_path,  # type: ignore[attr-defined]
                    cfg.vars,
                )
            else:
                raise AssertionError(f"Unexpected asset type {type(obj).__name__}")
            if cfg.datalad.enabled:
                ensure_datalad(ds, path, cfg.datalad.cfg_proc)
            paths = obj.download(Path(path))
            if isinstance(obj, BuildLog):
                logs_added += len(paths)
                if sanitize_secrets and cfg.secrets:
                    for p in paths:
                        sanitize(p, cfg.secrets, cfg.allow_secrets_regex)
            elif isinstance(obj, Artifact):
                artifacts_added += len(paths)
        if isinstance(cicfg, GitHubConfig) and cicfg.releases_path is not None:
            assert isinstance(ci, GitHubActions)
            for asset in ci.get_release_assets():
                path = asset.expand_path(cicfg.releases_path, cfg.vars)
                if cfg.datalad.enabled:
                    ensure_datalad(ds, path, cfg.datalad.cfg_proc)
                paths = asset.download(Path(path))
                relassets_added += len(paths)
        since_stamps[name] = ci.new_since().isoformat()
        log.debug("%s timestamp floor updated to %s", name, since_stamps[name])
    with open(state, "w") as fp:
        json.dump(since_stamps, fp)
    log.info("%d logs downloaded", logs_added)
    log.info("%d artifacts downloaded", artifacts_added)
    log.info("%d release assets downloaded", relassets_added)
    if cfg.datalad.enabled and (logs_added or artifacts_added or relassets_added):
        msg = f"[tinuous] {logs_added} logs added"
        if artifacts_added:
            msg += f", {artifacts_added} artifacts added"
        if relassets_added:
            msg += f", {relassets_added} release assets added"
        ds.save(recursive=True, message=msg)


@main.command("sanitize")
@click.argument(
    "path", type=click.Path(exists=True, dir_okay=False, writable=True), nargs=-1
)
@click.pass_obj
def sanitize_cmd(cfg: Config, path: List[str]) -> None:
    """ Sanitize secrets in logs """
    for p in path:
        sanitize(Path(p), cfg.secrets, cfg.allow_secrets_regex)


def sanitize(
    p: Path, secrets: Dict[str, Pattern], allow_secrets: Optional[Pattern]
) -> None:
    def replace(m: Match) -> str:
        s = m.group()
        assert isinstance(s, str)
        if allow_secrets is not None and allow_secrets.search(s):
            return s
        else:
            return "*" * len(s)

    log.info("Sanitizing %s", p)
    with InPlace(p, mode="t", encoding="utf-8", newline="") as fp:
        for i, line in enumerate(fp, start=1):
            for name, rgx in secrets.items():
                newline = rgx.sub(replace, line)
                if newline != line:
                    log.info("Found %s secret on line %d", name, i)
                line = newline
            fp.write(line)


def ensure_datalad(ds: Any, path: str, cfg_proc: Optional[str]) -> None:
    # `ds` is actually a datalad Dataset, but the import is optional.
    dspaths = path.split("//")
    if "" in dspaths:
        raise click.UsageError("Path contains empty '//'-delimited segment")
    for i in range(1, len(dspaths)):
        dsp = "/".join(dspaths[:i])
        if not Path(dsp).exists():
            ds.create(dsp, cfg_proc=cfg_proc)


if __name__ == "__main__":
    main()
