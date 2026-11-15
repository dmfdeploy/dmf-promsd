# Contributing to dmf-promsd

NetBox-driven Prometheus service-discovery for the dmfdeploy platform (dynamic monitoring targets from the NetBox SoT).

This repo is part of the **dmfdeploy platform**. GitHub is the canonical home and the
single source of truth: all changes land via **Pull Request** against `main`.

## Quick start

1. Read the platform overview in the [dmfdeploy/dmfdeploy](https://github.com/dmfdeploy/dmfdeploy) umbrella repo
   (`docs/architecture/dmfdeploy-platform-plan.md`) and apply any relevant ADRs from
   `docs/decisions/INDEX.md`.
2. Set up your machine and run the checks (see [Develop and test](#develop-and-test)).
3. Fork or branch, make your change on a topic branch, open a PR against `main`.
4. Ensure CI is green, your commits are **signed off** (see DCO below), and the
   PR body links its issue (see [What a pull request needs](#what-a-pull-request-needs)).

## Develop and test

This section is the one place for development and test commands. The README and
`AGENTS.md` point here. The commands match what CI runs in
`.github/workflows/ci.yml`.

### Prerequisites

- **Python 3.12 or later.** CI tests 3.12.
- **Optional tools.** [gitleaks](https://github.com/gitleaks/gitleaks) powers
  the commit hook.

You don't need a NetBox instance to run the checks. The tests use a fake NetBox
API.

### Set up

Run these commands from the repository root. Check first that `python3` is
version 3.12 or later. If it isn't, use the full name of a newer interpreter,
such as `python3.13`, in place of `python3`.

```bash
python3 --version
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
```

The install takes under a minute on a fresh checkout, depending on your network.

### Run the checks

Run one script from the repository root, with the virtual environment active:

```bash
scripts/check.sh
```

CI runs the same script, so a green run here means the `test` job passes. The
script takes about five seconds. It stops at the first failing step:

1. It fails on any tracked Python file over 400 lines.
2. It runs `ruff format --check .`.
3. It runs `ruff check .`.
4. It runs `pytest -q`, which has about 80 tests.

To fix a formatting failure, run `ruff format .` and check the result with
`git diff`.

CI also runs two checks outside this script, and you don't need to run them
before you push. The `working-model` job confirms that the working-model
block in `AGENTS.md`, `CLAUDE.md` and `QWEN.md` matches the umbrella template.
The `guard` workflow scans for secrets and vulnerabilities, lints the workflow
files, and checks commit subjects and the issue link. The `DCO` workflow checks
sign-offs.

### Run the service locally

Set the NetBox address and token, then start the service from the repository
root. The [README](README.md#run-it) says which token permissions the service
needs. To run it in a container instead, see the same section.

```bash
export NETBOX_URL="https://netbox.example.com"
export NETBOX_TOKEN="<your NetBox API token>"
uvicorn dmf_promsd.main:create_app --factory --host 0.0.0.0 --port 8000
```

The service refuses to start without `NETBOX_URL` and `NETBOX_TOKEN`. Take the
token from your secret store at run time, and keep it out of files in this
repository.

### Install the hooks

Run this once in each clone:

```bash
bin/install-hooks.sh
```

The script sets `core.hooksPath` to `.githooks/`. The `pre-commit` and
`pre-push` hooks scan your changes with gitleaks. They skip the scan with a
warning when gitleaks isn't installed. CI runs the same scan.

## Branch & PR model

- **GitHub Pull Requests only.** Direct push to `main` is blocked; force-push is
  banned; linear history is required.
- Topic branches: **`<handle>/<short-slug>`** (e.g. `jdoe/fix-probe-path`). One
  logical change per branch; rebase onto `main` rather than long-lived branches.
- A reviewer (per `CODEOWNERS`) and green required checks are needed to merge.

## What a pull request needs

CI checks each of these.

- **An issue link in the PR body.** Write a fully qualified reference to an issue
  in the [dmfdeploy/dmfdeploy](https://github.com/dmfdeploy/dmfdeploy/issues)
  backlog, such as `Refs dmfdeploy/dmfdeploy#912`. Use `Closes dmfdeploy/dmfdeploy#N`
  if the pull request finishes the issue. A bare `#N` doesn't count, because it
  points at this repo. If your change has no issue, such as a typo fix, a
  maintainer can add the `no-issue` label. Only maintainers can add labels, so
  if you open the pull request from a fork, ask in the PR. The check runs again
  when you edit the body or push a commit. Adding the label does not rerun it,
  so after the label is on, edit the body or push to clear a failed check.
- **A DCO sign-off on every commit.** Use `git commit -s`. See the next section.
- **Conventional commit subjects.** Use `type(scope): summary`, with one
  optional scope in lowercase. The types are `feat`, `fix`, `docs`, `chore`,
  `refactor`, `test`, `build`, `ci`, `perf`, `revert` and `style`. Other types,
  such as `wip`, are rejected, and so is a scope list such as `(api,ui)`.
- **Passing CI.** The `ci` workflow runs the checks in
  [Develop and test](#develop-and-test). The `guard` workflow adds secret and
  vulnerability scans.

Also leave agent session links out of commit messages and the PR body. The
`guard` workflow rejects one known session-link format. It can't recognize
every tool's links, so reviewers look for them too.

## Developer Certificate of Origin (DCO)

We use the [Developer Certificate of Origin](https://developercertificate.org/),
not a CLA. **Every commit must be signed off:**

```bash
git commit -s -m "fix: correct the probe path"
```

This appends a `Signed-off-by: Your Name <you@example.com>` trailer certifying you
have the right to submit the work under the project license. A **DCO check** runs
on every PR and fails if any commit is missing the trailer. Amend with
`git commit --amend -s` or rebase with `git rebase --signoff main` to fix. PRs are
**rebase-merged** by default so your signed-off commits land on `main` unchanged.

## Versioning & releases

This repo carries a `VERSION` file (single semver line). Per **ADR-0005**,
`VERSION` is the single source of truth — any release-tagged change must update it
in the same commit. **No VERSION bump → no release.** Release tags are `v<VERSION>`,
created by release automation, never by hand.

## Secrets & public-safety posture

**Secrets stay in OpenBao.** Never commit, track, or reference credentials, tokens,
keys, kubeconfigs, or Terraform state — not even with a "remove later" TODO. Use
**placeholder syntax** for any IPs, DNS names, or operator identity in code, docs,
PR descriptions, or issues (`<control-node-public-ip>`, `dmf.example.com`,
`<handle>`). A local pre-commit gitleaks hook runs on commit, and CI runs
secret-scanning + scrub gates on every PR — but redaction is your responsibility
first. If you need a secret, ask a maintainer — do not improvise a transport.

## Must / Must not

### MUST
- Open changes as **GitHub PRs against `main`** with **signed-off** commits.
- Use Conventional Commit messages and `<handle>/<short-slug>` topic branches.
- Update `VERSION` in the same commit as any release-tagged change.
- Use **placeholder syntax** for all IPs / DNS / operator identity in every artifact.

### MUST NOT
- Commit secrets, tokens, keys, kubeconfigs, or Terraform state.
- Push directly to `main`, force-push, or use `--no-verify` / `--no-gpg-sign`.
- Paste secrets, real IPs/DNS, or operator identity into issues, PRs, or CI logs.

## AI agent contract

Much of this platform is built by AI agents. Agents contribute the same way:
**PRs against `main`, signed off, CI green.** Additionally, agents must run cluster
mutation only via `bin/run-playbook.sh` (ADR-0010), must not use
`--no-verify`/`--force`/`--no-gpg-sign`, and must stop and ask before modifying a
sub-repo with uncommitted state.

## Reporting security issues

See [`SECURITY.md`](SECURITY.md). **Do not** open a public issue for a vulnerability.

## License & spec

Contributions are licensed under [Apache 2.0](LICENSE). The canonical governance
model is **ADR-0041 — DMF Release and Contribution Model** in the
[dmfdeploy/dmfdeploy](https://github.com/dmfdeploy/dmfdeploy)
umbrella repo (`docs/decisions/`).
