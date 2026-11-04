# Eagle: Central PR Review Agent Platform

Eagle is a team web app where members submit pull request links and the platform
launches an AI coding agent (Claude Code, Codex, or OpenCode) to review them.
Reviews are stored, viewable in the UI, optionally posted back to the PR, and
announced through Slack or email. It runs on Red Hat OpenShift and is deployed
through a custom operator that lives in this GitHub repo.

## Decisions

| Area | Decision |
|------|----------|
| Backend | Python 3.12, FastAPI, SQLAlchemy 2 (async), Alembic, Pydantic v2 |
| Tooling | `uv` for env and packages, `ruff` for lint/format, `pytest` via `uv run pytest` |
| Frontend | React + TypeScript + Vite + PatternFly 5 |
| Database | PostgreSQL (bundled by the operator or external via connection secret) |
| Agent execution | One Kubernetes Job per review, isolated pod, no cluster credentials |
| Operator | Go, operator-sdk, single `ReviewPlatform` CRD |
| Review output | Always stored in the app; posting a PR comment is an optional toggle |
| Roles | `admin`, `member`, `viewer` |
| Auth | Local username/password (phase 1), OpenShift OAuth / Red Hat SSO (last phase) |

## Architecture

```
            Route (external URL, TLS edge)
                        |
                 +-------------+
                 |  eagle-web  |  FastAPI API + built React UI
                 +-------------+
                   |         |
            PostgreSQL     eagle-worker (queue consumer, poller, notifier)
                                 |
                                 |  creates (k8s API, scoped SA)
                                 v
                  review Job pod (agent runner image)
                  - clones PR read-only
                  - runs claude / codex / opencode headless
                  - writes result back via callback token
```

### Components

- **eagle-web**: REST API (`/api/v1`), session auth, admin panel, serves the UI.
- **eagle-worker**: picks queued reviews from Postgres (`SELECT ... FOR UPDATE SKIP LOCKED`),
  launches Jobs, enforces concurrency limits and timeouts, runs the PR poller
  and dispatches notifications.
- **agent-runner image**: contains `git`, `gh`, Claude Code CLI, Codex CLI,
  OpenCode CLI and a small Python entrypoint that runs the selected agent and
  POSTs the result to eagle-web using a one-time, review-scoped token.
- **operator**: reconciles a `ReviewPlatform` CR into Deployments, Services,
  Route, Postgres StatefulSet (optional), Secrets, ServiceAccounts, RBAC,
  NetworkPolicies.

### Execution backends

The worker uses an `Executor` interface with two implementations:
- `KubernetesJobExecutor` (production)
- `LocalSubprocessExecutor` (local development only)

## Data model (initial)

- **users**: id, username, email, password_hash, role (`admin|member|viewer`), is_active, created_at
- **reviews**: id, pr_url, provider (`github`), repo, pr_number, head_sha, title, author,
  submitted_by, agent, model, auth_profile_id, custom_prompt, status
  (`queued|running|succeeded|failed|cancelled`), result_markdown, summary, verdict,
  token_usage, cost_estimate, error, posted_comment_url, created_at, started_at, finished_at
- **tracked_prs**: pr_url, last_seen_sha, poll_enabled, poll_interval_minutes, last_polled_at
- **agent_configs** (one row per agent: `claude|codex|opencode`): enabled, **default_model**,
  allowed_models (empty = any), credential_id, team review instructions, timeout_seconds.
  This is where admins assign default models to agents. A review uses the requested model,
  else the agent's default model.
- **inference_credentials**: name, kind (`anthropic_api_key|claude_subscription|vertex_ai|
  openai_api_key|chatgpt_subscription|openai_compatible`), encrypted_payload, metadata
  (project, region, base_url)
- **repo_settings**: repo pattern, default agent profile, custom instructions, post_comment
- **notification_channels**: kind (`slack|smtp`), config (encrypted), enabled, events
- **settings**: key/value for global config (concurrency, poller default off, etc.)
- **audit_log**: actor, action, target, details, timestamp

Secrets are encrypted at rest using Fernet with a key from a Kubernetes Secret
(`EAGLE_SECRET_KEY`). The API never returns decrypted secrets, only masked values.

## Inference auth matrix

| Agent | API key | Subscription | Vertex AI | OpenAI-compatible (MaaS) |
|-------|---------|--------------|-----------|---------------------------|
| Claude Code | `ANTHROPIC_API_KEY` | `CLAUDE_CODE_OAUTH_TOKEN` (`claude setup-token`) | `CLAUDE_CODE_USE_VERTEX=1` + SA JSON | via LiteLLM-style gateway |
| Codex | `OPENAI_API_KEY` | ChatGPT login `auth.json` | n/a | custom provider in `config.toml` |
| OpenCode | provider key | n/a | Vertex provider | OpenAI-compatible provider |

Note: shared use of a personal subscription by a team service may conflict with
provider consumer terms. The admin panel labels these options accordingly; API
key, Vertex AI and MaaS are the recommended options.

## Review prompt composition

The prompt sent to the agent is built in layers, most general first:

1. **System review instructions** (built in): how to review, output format, verdict,
   and that PR content is untrusted data.
2. **Agent instructions** (admin panel, per agent).
3. **Repo instructions** (admin panel, matched by repo pattern).
4. **Custom prompt** (optional, per review): extra context the submitter types in the
   submit form, for example "focus on the migration in db/", "this replaces the v1 API,
   backward compat is not needed", or a link to a design doc. Max 8000 characters.

The custom prompt is stored with the review, shown on the review detail page, and
copied when a review is re-run (manually or by the poller). It is placed in a clearly
delimited section so it cannot override the system instructions about untrusted content.

## Security model

- Agent Jobs run with a dedicated ServiceAccount that has no RBAC permissions and
  `automountServiceAccountToken: false`.
- Only the single credential needed is injected, as a per-Job Secret deleted after completion.
- GitHub token given to the Job is read-only. PR comments are posted by eagle-worker,
  never by the agent pod.
- NetworkPolicy limits Job egress to GitHub and inference endpoints (configurable).
- Resource limits, `activeDeadlineSeconds`, `ttlSecondsAfterFinished`.
- Review instructions tell the agent to treat PR content as untrusted data.
- Passwords hashed with argon2; sessions via signed HTTP-only cookies; CSRF protection.
- Audit log for every admin action.

## Phases

Status (2026-11-04): phases 0 to 8 are implemented, plus an OLM bundle and
catalog for OperatorHub installs. Tested with mocked model endpoints (fake agent
CLIs, fake GitHub/Slack/SMTP/OIDC/model APIs), the operator against a real
Kubernetes API server, and the full OperatorHub install, an upgrade and a review
Job on a local kind cluster with OLM. Not yet verified on OpenShift, against real
model APIs, or with a real IdP.

Planned but not built: the OpenShift OAuth proxy login option (SSO is generic
OIDC), notifying the PR author, and per-user notification preferences.
Notifications go to channels and, for SMTP, optionally to the submitter.

### Phase 0: Repo bootstrap
- Repo layout, `PLAN.md`, `README.md`, `.gitignore`, `.pre-commit-config.yaml`
- Backend skeleton (FastAPI app, config, health endpoints)
- Frontend skeleton (Vite + PatternFly)
- Containerfiles, Makefile, CI workflow (lint + test)

### Phase 1: Core app, local auth, manual reviews
- DB models + Alembic migrations
- Local auth, bootstrap admin from env/secret on first start
- User management (admin creates, disables, deletes users; assigns roles)
- Submit PR URL with optional custom prompt, review queue, status page,
  review detail with rendered markdown
- `LocalSubprocessExecutor` with Claude Code + API key
- PR history list with filters

### Phase 2: Agents and inference auth
- Claude Code, Codex, OpenCode runners
- All credential kinds from the matrix, configured in the admin panel
- Per-agent config (default model, allowed models, credential, instructions) and
  per-repo settings (instructions, default agent, PR comment default)
- Token usage and cost tracking

### Phase 3: OpenShift execution
- `KubernetesJobExecutor`, agent-runner image
- Concurrency limits, timeouts, cancel, retry
- NetworkPolicies, Job SA, secret lifecycle
- Kustomize manifests for a manual deploy

### Phase 4: Operator
- Go operator-sdk project, `ReviewPlatform` CRD
- Reconcile web, worker, Postgres (bundled or external), Route, RBAC, NetworkPolicy
- Status conditions, upgrade path, sample CR, install docs

### Phase 5: GitHub integration and polling
- GitHub App or PAT config in admin panel (private repos)
- Optional posting of review as a PR comment
- PR poller (disabled by default, interval configurable globally and per PR)
- Re-review on new head SHA only, incremental review since last reviewed SHA

### Phase 6: Notifications
- Slack (incoming webhook and bot token) and SMTP channels
- Notify submitter, PR author (if mapped), and/or channels on completion/failure
- Per-user notification preferences

### Phase 7: Observability and ops
- Prometheus metrics, structured logging, audit log UI
- Backup/restore docs for Postgres

### Phase 8: SSO
- OpenShift OAuth proxy and/or Red Hat SSO (OIDC)
- Group to role mapping, local accounts kept as break-glass

## Repo layout

```
eagle/
  backend/            FastAPI app, worker, alembic, tests (uv project)
  frontend/           React + PatternFly UI
  agent-runner/       Container image and entrypoint for review Jobs
  operator/           Go operator-sdk project, OLM bundle, kind test script
  deploy/             Install guide and Kustomize manifests
  .github/workflows/  CI
```
