# AGENTS.md

Guidance for coding agents (and humans) working in this repository. Code,
identifiers, and comments are in English; user-facing UI copy and
`README.md` are in German — keep it that way per surface. This file
complements `README.md`, it doesn't replace it: read the linked section
there when you need the full story.

## What this is

TAKT is an offline stopwatch for fire-brigade training/competitions. It has
two runtime contexts that share the `takt` Python package:

1. **Pi timer app** (`takt-server`) — runs on a Raspberry Pi (or a laptop in
   mock mode) as a local aiohttp web server.
2. **Fleet Registry** (`takt-registry`, `takt-agent`) — a FastAPI service
   (typically on a NAS/server, see `compose.yaml`) that centrally manages
   many Pis over an agent (`takt.management.agent`) running on each device:
   deployments, WiFi, health checks, diagnostics, maintenance actions.

## Architecture / ownership boundaries

```
src/takt/
  domain/         pure timing/run model (Duration, TimerSession, TimerState) — no I/O
  application/     use-case services (TimerController, AudioService, SystemPowerService,
                   RunCurationService) — orchestrate domain + persistence
  persistence/     SQLAlchemy models + Alembic migrations for takt.db (run data)
  web/             aiohttp server; routes/ is one module per concern
                   (core, runs, maintenance, security, audio, confirmations, common)
  management/      the Pi-resident agent (management/agent.py) + diagnostics redaction
  registry/        FastAPI Fleet Registry: fastapi_app.py wires routes, store/ is one
                   module per concern (devices, deployments, jobs, releases, mirrors,
                   maintenance), models.py + migrations/ own registry.db
webui/src/
  timer/, App.tsx  React UI for the Pi timer (built into src/takt/web/static/)
  fleet/           React UI for the Fleet Registry (built into
                   src/takt/registry/static/)
  shared/          code used by both (httpClient, contracts)
  shared/ui/       base component library (Button, Dialog, Select, ...),
                   backed by Radix UI Primitives; see its own tokens.css
                   for the --ui-* contract each app maps its palette onto
```

Keep changes inside their owning layer: domain stays free of I/O, route/store
modules stay scoped to one concern, and the two React entry points
(`timer`, `fleet`) only share code via `webui/src/shared/`.

## Generated files — never hand-edit

`src/takt/web/static/**` and `src/takt/registry/static/**` are Vite build
output, are **not** tracked in git (`.gitignore`), and must never be edited
directly. Regenerate them after any `webui/` change:

```bash
./scripts/build_web_ui.sh        # -> src/takt/web/static
./scripts/build_registry_ui.sh   # -> src/takt/registry/static
```

Each script installs npm deps if needed, then runs typecheck + lint + tests
+ build, so a script failure means the frontend isn't ready to ship. Python
tests run fine without built static assets (the browser-asset check is
skipped); the full suite (`tests/test_static_assets.py`) needs both built.

`design-system/styles.css` is also generated — from
`webui/src/shared/ui/tokens.css` + `ui.css`, via
`node scripts/build_design_bundle.mjs`. It mirrors the hosted "TAKT UI"
Claude Design project; run the script and re-upload after touching either
source file so the two can't drift apart.

## Commands

Setup:

```bash
python3 -m venv .venv && .venv/bin/python -m pip install -e '.[dev]'
cd webui && npm ci
```

| Task | Command |
|---|---|
| Python lint | `.venv/bin/ruff check .` |
| Python typecheck | `.venv/bin/mypy` |
| Python tests (full) | `PYTHONPATH=src .venv/bin/python -m unittest discover -s tests` |
| Python tests (pytest) | `.venv/bin/pytest` |
| Focused Python test | `.venv/bin/pytest tests/test_timer_controller.py -k some_case` |
| Frontend typecheck | `cd webui && npm run typecheck` |
| Frontend lint | `cd webui && npm run check` |
| Frontend tests | `cd webui && npm test` (timer) / `npm run test:fleet` (fleet) |
| Frontend build | `cd webui && npm run build` / `npm run build:fleet` |
| Dev servers | `./scripts/launch_web_dev.sh` (web, needs `build_web_ui.sh` first), `./scripts/launch_registry.sh` |
| shared/ui component gallery | `./scripts/launch_ui_gallery.sh` or `cd webui && npm run dev:ui` — dev-only, never built into either app's static assets. `TAKT_UI_HOST`/`TAKT_UI_PORT` override the default `127.0.0.1:5175`. |
| Pi transport package | `./scripts/package_for_raspberry_pi.sh` |

Hardware-/environment-specific (do not expect these to run in a normal dev
sandbox):

- `./scripts/install_raspberry_pi.sh`, `./scripts/deploy_to_raspberry_pi.sh` — target real Pi hardware over SSH.
- GPIO button tests marked as such, and anything touching `gpiozero`/`lgpio` — real GPIO only.
- `systemctl`, `takt-maintenance-helper`, poweroff/reboot paths — require the installed systemd units and root helper on a Pi.

CI (`.github/workflows/registry-image.yml`) runs `ruff check src tests
scripts/takt_wifi_helper.py`, `mypy`, `pytest`, and the full `webui` script
sequence (`typecheck && check && test && build && build:fleet`) — match
that before pushing.

## Database migrations

Two independent SQLite databases, each Alembic-managed: `takt.db` (Pi run
data, `alembic_runs.ini`) and `registry.db` (Fleet Registry,
`alembic_registry.ini`). `models.py` in `persistence/` and `registry/` is
the source of truth for schema; migrations apply it. Changing a table:
edit `models.py`, generate a revision (`python -m alembic -c
alembic_registry.ini revision --autogenerate -m "..."`), review the
generated file, then `alembic upgrade head` + tests locally. Registry
migrations must stay idempotent against a partially-migrated database (use
`create_table_if_missing`/`add_column_if_missing` from
`registry/migrations/_helpers.py`) — see [Datenbankmigrationen](README.md#datenbankmigrationen)
for why.

## Conventions

- Python: `from __future__ import annotations`, dataclasses for value
  objects, full type hints (mypy runs with `check_untyped_defs`), ruff
  rule set in `pyproject.toml` (`E,F,I,UP,B,C4,PERF,PIE,RUF,SIM`).
  Docstrings only where behavior is non-obvious.
- Frontend: TypeScript for new code (a few legacy `.js` files remain in
  `fleet/`); colocate `*.test.ts(x)` next to the module under test.
- UI copy is German and must stay accessible (existing components use
  semantic HTML, ARIA labels, keyboard operability) — match that when
  adding UI.
- Route/store modules follow one-file-per-concern; add new endpoints to
  the matching module rather than growing `core`/`common`.

## Security & data-safety invariants

- Never expose credentials: admin passwords, device tokens, and secrets
  are redacted before diagnostics leave a Pi (`management/redaction.py`) —
  redact on the agent, never rely on the Registry to do it.
- Never commit real credentials, tokens, or `.env` files; use
  `.env.example`/`config.example.toml` as templates.
- Preserve run databases and configuration: don't write migrations or code
  paths that could drop/truncate `takt.db`/`registry.db` data or bypass
  the daily backup (`persistence/backup_service.py`).
- Device actions (service/power control, deployments) must stay
  authorized and auditable — go through the existing job/maintenance-lock
  flow, never bypass the maintenance-lock check that protects an in-progress
  or unsaved run (see [Wartung und Wiederherstellung über den Fleet Manager](README.md#wartung-und-wiederherstellung-über-den-fleet-manager)).
- Remote registry/agent traffic assumes HTTPS or a private VPN; don't add
  code paths that silently allow plaintext credentials over a non-loopback
  connection (see [Netzwerk und Sicherheit](README.md#netzwerk-und-sicherheit)).

## Working in this worktree

- Check `git status` before touching anything; this repo is often worked
  on from multiple parallel agent worktrees at once (e.g.
  `.worktrees/issue-N-implementation/`, `issue-N-worktree/`). Stay inside
  your own worktree — never read from or write into a sibling worktree
  directory, and don't assume its uncommitted state is yours to change.
- `src/takt/web/static/`, `src/takt/registry/static/`, `build/`, `dist/`,
  `bundled-release/*.tar.gz*`, `.venv/`, `webui/node_modules/`, and
  `*.dev.db` are generated/local and gitignored — don't hand-edit or commit
  them, and don't assume they're shared across worktrees (each one builds
  its own).
- If you find unfamiliar uncommitted files or branches, that's likely
  another agent's in-progress work — investigate before deleting or
  overwriting it.
