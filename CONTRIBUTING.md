# Contributing

Thanks for considering a contribution. This is the maintained fork of
[pysolarcloud](https://github.com/bugjam/pysolarcloud), published to PyPI as
`sungrow-isolarcloud` and consumed mainly by the
[sungrow-hass](https://github.com/KRoperUK/sungrow-hass) Home Assistant integration.

## Development setup

Python 3.12 is the floor (`requires-python`); CI tests 3.12, 3.13 and 3.14.

```
git clone https://github.com/KRoperUK/pysolarcloud.git
cd pysolarcloud
python -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
```

`[dev]` is the single source of truth for the lint/type/test tooling pins. CI installs the
same extra, so local results match CI, and Dependabot keeps those pins current — don't add a
second copy of them to a workflow.

## Checks

Run what CI runs before you push:

```
ruff check src/ tests/
ruff format --check src/ tests/     # ruff format src/ tests/ to apply
mypy
pytest --cov=src/pysolarcloud --cov-report=term-missing
```

- `lint` runs ruff and mypy on Python 3.13.
- `test` runs pytest on 3.12, 3.13 and 3.14. Coverage has a `fail_under` floor, so a change
  that adds code without tests fails CI.

## Commits must be signed

The `main` ruleset enforces **`required_signatures`**, so an unsigned commit cannot be merged
even if every check passes — GitHub reports *"Commits must have verified signatures"* on the
pull request.

Configure signing once:

```
git config --global commit.gpgsign true
git config --global tag.gpgsign true
git config --global user.signingkey <FINGERPRINT>
gpg --armor --export <FINGERPRINT>       # paste into GitHub → Settings → SSH and GPG keys
```

Then confirm what you are about to push is verified:

```
git log --show-signature -1
```

GitHub shows a **Verified** badge next to each signed commit. If you rebase or amend, re-check:
rewriting history can drop the signature.

## Pull requests

`main` also requires:

- a pull request — direct pushes, force-pushes and deletion are all blocked;
- the `lint`, `test (3.12)` and `test (3.13)` checks to pass (3.14 runs but is not required);
- **the branch to be up to date with `main`** (strict status checks), so rebase onto `main`
  rather than merging it in, and expect to re-run checks after a rebase.

Merging is allowed by merge commit, squash or rebase — squash is preferred for a branch with
fixup commits.

A pull request containing commits **not attributed to you** (pairing, tooling, a co-author
trailer) needs an extra approval, because the ruleset sets
`require_extra_approval_for_unattributed_changes`. Keep the author of your commits your own
account where you can.

## Releases

Releases are automated with [release-please](https://github.com/googleapis/release-please):
every push to `main` updates a release pull request that bumps the version in
`pyproject.toml`, updates `CHANGELOG.md` and, when merged, tags the release and publishes to
PyPI through Trusted Publishing (`release-please.yml`).

That means **commit messages drive the version** — use Conventional Commits
(`fix:`, `feat:`, `feat!:`/`BREAKING CHANGE:`), since `fix:` yields a patch and `feat:` a
minor (`bump-minor-pre-major` is on, so pre-1.0 breaking changes still move the minor).

`.github/workflows/publish.yml` is a manual backup path for publishing an existing tag; normal
releases do not need it.
