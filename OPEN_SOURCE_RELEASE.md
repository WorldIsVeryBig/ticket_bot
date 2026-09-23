# Open Source Release

This repo is currently a private working tree with operational configs, local runbooks,
and historical secrets mixed into the normal development history. Treat the public
release as a curated snapshot, not as a direct visibility flip of this repository.

## Recommended Publication Model

1. Rotate any secrets that have ever appeared in tracked files.
2. Export a clean snapshot with `scripts/release/export_public_snapshot.sh`.
3. Publish that snapshot to a new public repository or an orphan release branch.
4. Tag the first public release as `v2.0.0`.

## Why A Snapshot Instead Of Flipping This Repo Public

- `config.aws-tokyo.yaml` has contained proxy credentials in tracked history.
- Private operational files such as `config.local.yaml`, `config.cloud.yaml`,
  `config.aws-tokyo.yaml`, `RUNBOOK.md`, and `scripts/deploy/` are environment-specific.
- Training data and model assets are large and not required for a first public release.

## Intended Public Surface

- `src/`
- `tests/`
- `pyproject.toml`
- `README.md`
- `.env.example`
- `config.yaml.example`
- `config.local.example.yaml`
- `config.cloud.example.yaml`
- `config.aws-tokyo.example.yaml`
- `docker-compose.example.yml`
- `VERSIONING.md`
- `LICENSE`

## Keep Private Or Exclude From The Public Snapshot

- `config.local.yaml`
- `config.cloud.yaml`
- `config.aws-tokyo.yaml`
- `config.tg-bot.yaml`
- `RUNBOOK.md`
- `scripts/deploy/`
- `captcha_training_data/`
- `data/`
- `model/`
- `ticket-filter/`
- Browser profiles, cookies, logs, and any local runtime artifacts

## Pre-Release Checklist

- Replace sample values in copied config files with your own environment values.
- Review the generated snapshot one more time for tokens, URLs with credentials, and local paths.
- Confirm the chosen license matches how you want others to use the project.
- Only tag `v2.0.0` after the public snapshot is the exact tree you want to publish.
