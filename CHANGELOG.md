# 0.5.2 (Tue Apr 19 2022)

#### 🐛 Bug Fix

- Retry all GitHub requests that result in a 5xx [#146](https://github.com/con/tinuous/pull/146) ([@jwodder](https://github.com/jwodder))

#### 🧪 Tests

- Test against Python 3.10 [#136](https://github.com/con/tinuous/pull/136) ([@jwodder](https://github.com/jwodder))

#### Authors: 1

- John T. Wodder II ([@jwodder](https://github.com/jwodder))

---

# 0.5.1 (Wed Jan 26 2022)

#### 🐛 Bug Fix

- Treat Travis jobs with "started" status as incomplete [#144](https://github.com/con/tinuous/pull/144) ([@jwodder](https://github.com/jwodder))
- Retry downloads that fail with ConnectionError [#134](https://github.com/con/tinuous/pull/134) ([@jwodder](https://github.com/jwodder) [@yarikoptic](https://github.com/yarikoptic))
- Retry Github.get_repo() requests that fail with 502 [#139](https://github.com/con/tinuous/pull/139) ([@jwodder](https://github.com/jwodder))
- Log tinuous version at start of run [#133](https://github.com/con/tinuous/pull/133) ([@jwodder](https://github.com/jwodder))
- Retry downloads of invalid zipfiles [#132](https://github.com/con/tinuous/pull/132) ([@jwodder](https://github.com/jwodder))

#### 🏠 Internal

- Update codecov action to v2 [#137](https://github.com/con/tinuous/pull/137) ([@jwodder](https://github.com/jwodder))
- Replace flake8-import-order-jwodder with isort [#140](https://github.com/con/tinuous/pull/140) ([@jwodder](https://github.com/jwodder) [@yarikoptic](https://github.com/yarikoptic))

#### 📝 Documentation

- Link to tinuous-inception in README [#128](https://github.com/con/tinuous/pull/128) ([@jwodder](https://github.com/jwodder))

#### 🧪 Tests

- Ignore "unreachable" false-positives from mypy [#142](https://github.com/con/tinuous/pull/142) ([@jwodder](https://github.com/jwodder))

#### Authors: 2

- John T. Wodder II ([@jwodder](https://github.com/jwodder))
- Yaroslav Halchenko ([@yarikoptic](https://github.com/yarikoptic))

---

# 0.5.0 (Fri Jul 09 2021)

#### 🚀 Enhancement

- Escape branch names, workflow names, and Appveyor job envs with percent encoding [#123](https://github.com/con/tinuous/pull/123) ([@jwodder](https://github.com/jwodder))

#### 🐛 Bug Fix

- Wait & retry on rate limit errors for GitHub requests [#126](https://github.com/con/tinuous/pull/126) ([@jwodder](https://github.com/jwodder))

#### 📝 Documentation

- Minor README tweaks [#122](https://github.com/con/tinuous/pull/122) ([@jwodder](https://github.com/jwodder))

#### 🧪 Tests

- Install git-annex through neurodebian for more recent version [#127](https://github.com/con/tinuous/pull/127) ([@jwodder](https://github.com/jwodder))

#### Authors: 1

- John T. Wodder II ([@jwodder](https://github.com/jwodder))

---

# 0.4.0 (Tue Jun 22 2021)

#### 🚀 Enhancement

- Add `{timestamp}` and `{timestamp_local}` placeholders [#114](https://github.com/con/tinuous/pull/114) ([@jwodder](https://github.com/jwodder))

#### 🐛 Bug Fix

- Recognize "canceled" status [#120](https://github.com/con/tinuous/pull/120) ([@jwodder](https://github.com/jwodder))
- Save changes in DataLad if only the statefile was modified [#121](https://github.com/con/tinuous/pull/121) ([@jwodder](https://github.com/jwodder))
- Sleep when search rate limit reached [#117](https://github.com/con/tinuous/pull/117) ([@jwodder](https://github.com/jwodder))
- Delay opening of config file to actual command execution [#116](https://github.com/con/tinuous/pull/116) ([@jwodder](https://github.com/jwodder))

#### 📝 Documentation

- Specify that the regexes are Python regexes and link to the Python docs [#110](https://github.com/con/tinuous/pull/110) ([@jwodder](https://github.com/jwodder))
- Add docstring to `__init__.py` [#109](https://github.com/con/tinuous/pull/109) ([@jwodder](https://github.com/jwodder))
- Update GitHub token regex in sample config [#108](https://github.com/con/tinuous/pull/108) ([@jwodder](https://github.com/jwodder))
- Fix README formatting [#115](https://github.com/con/tinuous/pull/115) ([@jwodder](https://github.com/jwodder))
- Add Anaconda badge and installation command [#107](https://github.com/con/tinuous/pull/107) ([@jwodder](https://github.com/jwodder))
- Document how to integrate with cron [#106](https://github.com/con/tinuous/pull/106) ([@jwodder](https://github.com/jwodder))

#### 🧪 Tests

- Test WorkflowSpec.match() [#112](https://github.com/con/tinuous/pull/112) ([@jwodder](https://github.com/jwodder))
- Test removeprefix [#111](https://github.com/con/tinuous/pull/111) ([@jwodder](https://github.com/jwodder))
- Update config file name in inception-test workflow [#103](https://github.com/con/tinuous/pull/103) ([@jwodder](https://github.com/jwodder))

#### Authors: 1

- John T. Wodder II ([@jwodder](https://github.com/jwodder))

---

# 0.3.0 (Sat Jun 12 2021)

#### 🚀 Enhancement

- Allow `since` setting to override state file if newer [#102](https://github.com/con/tinuous/pull/102) ([@jwodder](https://github.com/jwodder))
- [BREAKING] Rename default config file to `tinuous.yaml` [#101](https://github.com/con/tinuous/pull/101) ([@jwodder](https://github.com/jwodder))
- Rename state file; update state file after each CI system finishes [#100](https://github.com/con/tinuous/pull/100) ([@jwodder](https://github.com/jwodder))
- [BREAKING] Redo path specifications [#98](https://github.com/con/tinuous/pull/98) ([@jwodder](https://github.com/jwodder))
- [BREAKING] Replace `{type_id}` for releases with `{release_tag}` [#97](https://github.com/con/tinuous/pull/97) ([@jwodder](https://github.com/jwodder))
- Make the "types" setting optional [#96](https://github.com/con/tinuous/pull/96) ([@jwodder](https://github.com/jwodder))
- Add "manual" event type [#95](https://github.com/con/tinuous/pull/95) ([@jwodder](https://github.com/jwodder))
- Add "Produced by tinuous" message to Datalad commit messages [#86](https://github.com/con/tinuous/pull/86) ([@jwodder](https://github.com/jwodder))
- Allow specifying GitHub workflow inclusions & exclusions with regexes [#80](https://github.com/con/tinuous/pull/80) ([@jwodder](https://github.com/jwodder))
- Add --version option [#81](https://github.com/con/tinuous/pull/81) ([@jwodder](https://github.com/jwodder))
- Add `{job_index}`, `{job_env}`, and `{job_env_hash}` placeholders [#73](https://github.com/con/tinuous/pull/73) ([@jwodder](https://github.com/jwodder))
- Add "until:" config setting [#76](https://github.com/con/tinuous/pull/76) ([@jwodder](https://github.com/jwodder))
- Fill in `{commit}` for Travis PR builds by querying GitHub [#68](https://github.com/con/tinuous/pull/68) ([@jwodder](https://github.com/jwodder))
- Rename `{commit}` to `{build_commit}`; `{commit}` now refers to triggering commit [#64](https://github.com/con/tinuous/pull/64) ([@jwodder](https://github.com/jwodder))
- Make datalad an extra dependency [#63](https://github.com/con/tinuous/pull/63) ([@jwodder](https://github.com/jwodder))
- Support reading env vars from .env files [#59](https://github.com/con/tinuous/pull/59) ([@jwodder](https://github.com/jwodder))
- Eliminate `{abbrev_commit}` in favor of `{commit[:7]}` slicing [#62](https://github.com/con/tinuous/pull/62) ([@jwodder](https://github.com/jwodder))

#### 🐛 Bug Fix

- Retry downloads interrupted by connnection resets [#91](https://github.com/con/tinuous/pull/91) ([@jwodder](https://github.com/jwodder))
- Skip GitHub logs that return 410 [#87](https://github.com/con/tinuous/pull/87) ([@jwodder](https://github.com/jwodder))
- Don't expand unused vars [#79](https://github.com/con/tinuous/pull/79) ([@jwodder](https://github.com/jwodder))
- Better `{commit}` lookup for Travis builds [#71](https://github.com/con/tinuous/pull/71) ([@jwodder](https://github.com/jwodder))
- Sleep on & retry requests that return 5xx [#56](https://github.com/con/tinuous/pull/56) ([@jwodder](https://github.com/jwodder))
- Try fetching PR info from "List pull requests associated with a commit" endpoint [#53](https://github.com/con/tinuous/pull/53) ([@jwodder](https://github.com/jwodder))
- Cache PRs corresponding to commit hashes [#52](https://github.com/con/tinuous/pull/52) ([@jwodder](https://github.com/jwodder))

#### 🏠 Internal

- Reorganize code [#66](https://github.com/con/tinuous/pull/66) ([@jwodder](https://github.com/jwodder))

#### 📝 Documentation

- DOC: a more kosher casing of DataLad [#105](https://github.com/con/tinuous/pull/105) ([@yarikoptic](https://github.com/yarikoptic))
- Doc fix: Custom placeholders don't have to be defined in order any more [#99](https://github.com/con/tinuous/pull/99) ([@jwodder](https://github.com/jwodder))

#### 🧪 Tests

- Add test run with Datalad [#89](https://github.com/con/tinuous/pull/89) ([@jwodder](https://github.com/jwodder))
- Add workflow for running tinuous on tinuous [#65](https://github.com/con/tinuous/pull/65) ([@jwodder](https://github.com/jwodder))

#### Authors: 2

- John T. Wodder II ([@jwodder](https://github.com/jwodder))
- Yaroslav Halchenko ([@yarikoptic](https://github.com/yarikoptic))

---

# 0.2.0 (Mon May 17 2021)

#### 🚀 Enhancement

- Support downloading GitHub release assets [#48](https://github.com/con/tinuous/pull/48) ([@jwodder](https://github.com/jwodder))
- Support fetching build artifacts from GitHub Actions [#47](https://github.com/con/tinuous/pull/47) ([@jwodder](https://github.com/jwodder))
- Replace `path_prefix` with `vars` mapping [#46](https://github.com/con/tinuous/pull/46) ([@jwodder](https://github.com/jwodder))
- Add {abbrev_commit} placeholder [#41](https://github.com/con/tinuous/pull/41) ([@jwodder](https://github.com/jwodder))
- Look up PR numbers for GitHub PR workflows that are missing data [#29](https://github.com/con/tinuous/pull/29) ([@jwodder](https://github.com/jwodder))
- Add `path_prefix` config option [#37](https://github.com/con/tinuous/pull/37) ([@jwodder](https://github.com/jwodder))
- Make `workflows` optional [#36](https://github.com/con/tinuous/pull/36) ([@jwodder](https://github.com/jwodder))

#### 🏠 Internal

- Set up auto [#35](https://github.com/con/tinuous/pull/35) ([@jwodder](https://github.com/jwodder))
- Add a .gitignore file [#32](https://github.com/con/tinuous/pull/32) ([@jwodder](https://github.com/jwodder))

#### 📝 Documentation

- Start CHANGELOG [#33](https://github.com/con/tinuous/pull/33) ([@jwodder](https://github.com/jwodder))

#### 🧪 Tests

- Fix mypy configuration [#34](https://github.com/con/tinuous/pull/34) ([@jwodder](https://github.com/jwodder))
- Update tests for change in repo structure [#31](https://github.com/con/tinuous/pull/31) ([@jwodder](https://github.com/jwodder))

#### Authors: 1

- John T. Wodder II ([@jwodder](https://github.com/jwodder))

---

# v0.1.0 (2021-04-27)

Initial release
