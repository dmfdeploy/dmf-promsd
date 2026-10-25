# AGENTS.md — dmf-promsd

dmf-promsd is a small FastAPI service that reads NetBox and serves Prometheus
`http_sd` target groups from an in-memory cache (ADR-0038). It is a component of
the dmfdeploy platform. The [umbrella repo](https://github.com/dmfdeploy/dmfdeploy)
holds status, decisions and plans. "DMF" means the EBU Dynamic Media Facility.

## Boot steps

1. In the umbrella repo, run `bin/generate-status.sh`, then read
   `STATUS.digest.local.md`.
2. Read the tracking issue you claim, comments included, then
   [README.md](README.md) and [CONTRIBUTING.md](CONTRIBUTING.md).

## Build and test

Python 3.12 or later. CI (`.github/workflows/ci.yml`) runs:

```sh
python -m pip install -e '.[dev]'
scripts/check.sh
```

`scripts/check.sh` fails on any tracked Python file over 400 lines, then runs
`ruff format --check`, `ruff check`, and `pytest`.

Run `bin/install-hooks.sh` once per clone. `scripts/publish-to-ghcr.sh` builds
the image; `--push` publishes it.

## Rules that are easy to miss

- `VERSION` is the single source of truth (ADR-0005). Update it in the same
  commit as any release-tagged change. Never create release tags by hand.
- Sign off every commit (`git commit -s`) and use Conventional Commit messages.
- Never commit secrets, tokens, kubeconfigs or Terraform state. Use placeholders
  such as `dmf.example.com` for IPs, DNS names and operator identity.
- Never push directly to `main`, force-push, or use `--no-verify`.
- Post shared notes to [umbrella issue 618](https://github.com/dmfdeploy/dmfdeploy/issues/618).

<!-- WORKING-MODEL-BLOCK-START — generated from umbrella docs/templates/working-model-block.md; do not edit copies, edit the template and run bin/check-working-model-sync.sh -->
## Working model (mandatory)

Canonical: [docs/WORKING-MODEL.md](https://github.com/dmfdeploy/dmfdeploy/blob/main/docs/WORKING-MODEL.md)
in the umbrella repo. The three rules that matter mid-task:

1. **Work starts at an issue** in the canonical backlog
   ([dmfdeploy/dmfdeploy issues](https://github.com/dmfdeploy/dmfdeploy/issues)):
   `component:*`/`workstream:*` labels **always**, plus a milestone **only if
   the work is scheduled** — unscheduled work gets `platform-debt` and no
   milestone (§2). Non-trivial work gets a plan doc in umbrella `docs/plans/`
   with `tracking_issue` frontmatter.
2. **The completing PR auto-closes its issue; you still flip the plan
   frontmatter by hand in that PR.** Reference umbrella issues **fully
   qualified** — `Closes dmfdeploy/dmfdeploy#N` (bare `#N` targets the wrong
   repo); the daily issue-close reconciler honors that ref, cross-repo
   included. Manual close is a fallback.
3. **Never invent a local backlog** (TODO files, ad-hoc trackers). Issues =
   liveness; plan frontmatter = design state; ADRs = decisions (RFC in
   Discussions first); STATUS.md = pointer file (operator notes live in pinned issue dmfdeploy/dmfdeploy#618);
   STATUS.local.md = live repo snapshot.
<!-- WORKING-MODEL-BLOCK-END -->
