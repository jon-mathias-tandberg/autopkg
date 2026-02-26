# AGENTS.md

## Project overview

AutoPkg is a macOS software packaging automation framework. It is a pure Python CLI tool with no web services or Docker dependencies. See `ReadMe.md` for general info and `CONTRIBUTING.md` for code style requirements.

## Cursor Cloud specific instructions

### Python version

This codebase **requires Python 3.10** (not 3.12+). The `autopkglib/__init__.py` module uses the `imp` module and `distutils.version.LooseVersion`, both removed in Python 3.12. CI uses Python 3.10.11 (see `.github/workflows/tests.yaml`).

On the Cloud VM, Python 3.10 is installed via `deadsnakes/ppa` and a virtualenv is at `/workspace/.venv`. Always activate it:

```bash
source /workspace/.venv/bin/activate
```

### Running tests

```bash
source /workspace/.venv/bin/activate
python Scripts/run_tests.py
```

Expected: ~739 tests pass. 2 errors from `Foundation` (macOS-only `NSDictionary`) and ~12 skips are normal on Linux.

### Running lint

```bash
source /workspace/.venv/bin/activate
pre-commit run --all-files
```

Runs black, isort, and flake8 (see `.pre-commit-config.yaml`).

### Running the CLI

```bash
source /workspace/.venv/bin/activate
python Code/autopkg help
python Code/autopkg version
python Code/autopkg list-processors --prefs tests/preferences.plist
```

`WARNING: Did not load any default preferences.` and `WARNING: Failed 'from Foundation import ...'` are expected on Linux.

### Config file

An empty config file must exist at `~/.config/Autopkg/config.json` for the CLI and tests to work on Linux. The update script creates it automatically.

### Key caveats

- The root `requirements.txt` has `--no-binary :all:` which forces source builds. Use `.github/workflows/requirements.txt` (identical but without that flag) for faster installs.
- macOS-only packages (`pyobjc-*`) are skipped automatically on Linux via platform markers.
- E2E tests (`Code/tests/e2e_tests.sh`) interact with git repos and network; unit tests are self-contained.
