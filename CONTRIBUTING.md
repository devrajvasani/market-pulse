# Contributing to MarketPulse

Lightweight Git Flow (plan Section M). It's slightly more than a solo project
strictly needs — we use it on purpose, to practise the professional workflow.
You may review and merge your own PRs.

## Branches
| Branch | Purpose |
|---|---|
| `main` | Release target — always deployable, **protected** (PR + passing CI required), each release tagged (`v1.0.0`). The deploy pipeline ships it to AWS. |
| `develop` | Day-to-day integration branch (your working default). |
| `feature/<module>` | Module work off `develop` — e.g. `feature/ingestion`, `feature/streaming`, `feature/transform`, `feature/rag`. |
| `fix/<short-name>` | A focused bug fix (small fixes may go straight on `develop`). |
| `release/<version>` | **Temporary** release-prep branch, cut from `develop` near a milestone (only fixes + version bump + changelog). Created at release time, not now. |

### Flow
```
feature/<module> ─(PR,CI)─► develop ─(cut)─► release/1.0.0 ─(PR + tag v1.0.0)─► main ─► deploy ─► AWS
```
1. Branch off `develop`; commit as you go (CI runs on the branch).
2. Open a PR into `develop`; merge when CI is green.
3. At a milestone, cut `release/X.Y.Z`, stabilise, PR into `main`, `git tag vX.Y.Z`, merge back to `develop`.

## Commit messages — Conventional Commits
`type(scope): short summary in present tense`
- **type:** `feat`, `fix`, `docs`, `test`, `refactor`, `chore`, `ci`, `perf`, `build`, `style`.
- **scope:** the module — `ingestion`, `streaming`, `transform`, `rag`, `infra`, `cicd`, `docs`.

Examples:
- `feat(ingestion): add idempotent daily price loader`
- `ci(github): block deploy when critical tests fail`
- `docs(readme): add idempotency design summary`

Rules of thumb: one logical change per commit; summary < ~72 chars; explain *why* in the
body if not obvious; **never commit secrets or large data files** (they're gitignored).

## Approvals & safety (hard rules — see `CLAUDE.md`)
- **Commits/pushes are human-approved.** Claude Code stages changes and proposes a commit
  message; **you** run `git commit` / `git push` (Section M.7).
- **AWS resources are human-created.** Claude writes Terraform and may run `terraform plan`;
  **you** run `terraform apply` / `destroy` (Section N).
- Before committing, the pre-commit gate runs: `uv run pre-commit install` once, then ruff +
  critical tests + secret scanning run automatically on every commit.
