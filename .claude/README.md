# `.claude/` — Claude Code config (MarketPulse)

This folder configures Claude Code for the MarketPulse build. Place the whole `.claude/` folder at your **repo root**. (Claude Code ignores this README — it's just for you.)

## `skills/` — loaded automatically when relevant
On-demand instruction files. Claude Code reads the `description` in each and loads the skill when your task matches.
- **aws-cost-guard** — the $99-budget cost rules + cost-trap warnings. Loads when infra/cost is involved.
- **iac-terraform** — Terraform naming/tags, least-privilege IAM, the secret-in-state trap. Loads when writing `.tf` files.
- **data-quality** — idempotency, dbt tests, the Silver→Gold quality gate. Loads when building transforms/tests.

## `agents/` — review helpers (run before deploys)
Separate reviewers that run in their own context. Set to **Sonnet** (cheaper) and **read-only**.
- **cost-reviewer** — checks a change's cost vs the $99 budget; gives a verdict.
- **infra-reviewer** — checks IAM/security/secrets/naming; gives a verdict.

## Still to add (NOT in this folder)
- **`CLAUDE.md`** at repo root — the always-on rules: no-commit-without-approval, no-AWS-provisioning, ask-don't-assume, naming/tags, and the core engineering standards. The skills/agents above assume these exist.
- *(optional)* a **`PreToolUse` hook** to hard-block `git commit` / `terraform apply` as a backstop.

## Tips
- After placing the folder, run **`/agents`** inside Claude Code to confirm both reviewers loaded.
- Editing an agent file **on disk** requires a **session restart** to take effect (or use the `/agents` menu, which loads immediately).
- Skills trigger by `description` match, or you can force one with `/aws-cost-guard`, etc.
