# Contrechamp

**Producing a video? MANDATORY: Read [`AGENT_GUIDE.md`](AGENT_GUIDE.md) before responding to ANY
production request.**

Do not act on a production request until you have read AGENT_GUIDE.md.
It contains routing rules that determine your first action based on what the user asked.
Skipping it WILL cause you to take the wrong action.

## Developing Contrechamp itself

This repository is Contrechamp, an independent project derived from OpenMontage (see `NOTICE`).
When the session is about changing its code, tests or docs — not about making a video —
do **not** run the production flow (pipeline selection, preflight menu, human gates). Instead:

- Architecture and conventions: [`PROJECT_CONTEXT.md`](PROJECT_CONTEXT.md).
- Fork work so far and its rules: [`docs/fork/lot1-checks-that-bite.md`](docs/fork/lot1-checks-that-bite.md).
- A check ships with the test that makes it fail on a real defect and a test where it stays
  silent on the healthy case; see a test fail before trusting it.
- Run `pytest tests` before every commit; `make test-contracts` for schema/pipeline changes.
- Keep changes proposed upstream focused: one logical concern per pull request.

Personal, machine-specific notes, if any, live in a gitignored `CLAUDE.local.md` loaded after this file.
