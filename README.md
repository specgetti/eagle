# Eagle

A central pull request review platform for a team. Teammates paste a GitHub PR
link, and Eagle runs an AI coding agent (Claude Code, Codex or OpenCode) against
it in an isolated pod. The review is stored, shown in the web UI, and optionally
posted to the PR and announced in Slack or by email.

Eagle runs on OpenShift and is installed and managed by its own operator.

```
 Route (TLS) --> eagle-web (UI + API) --> PostgreSQL
                       ^                      ^
     result callback   |                      |
                       |               eagle-worker  (queue, timeouts, PR poller,
                       |                      |       comments, notifications)
                       |                      v  one Job per review
                       +-------------- review pod: clones the PR, runs the agent CLI
                                       (no cluster access, private ranges blocked)
```

## Features

- **Reviews**: PR URL, agent, model, optional extra context for the reviewer,
  optional PR comment. Re-run, cancel, filter, and incremental re-reviews of new
  commits by the PR poller (off by default). Each review shows the model the
  agent reported using and where it came from (picked, agent default, or the
  CLI's own default).
- **Agents**: Claude Code, Codex and OpenCode, each with a credential, a default
  model, the models members may pick, team instructions and a timeout.
- **Model access**: Anthropic API key, Claude subscription token, Vertex AI,
  OpenAI API key, ChatGPT subscription, and any OpenAI-compatible or
  Anthropic-compatible endpoint (a MaaS, LiteLLM, vLLM). Admins can **Test** a
  credential and **fetch the models** an endpoint serves.
- **Users**: admin, member and viewer roles; local accounts and optional OIDC
  single sign-on with group to role mapping. Audit log.
- **Integrations**: GitHub and GitHub Enterprise tokens, Slack (webhook or bot)
  and SMTP notifications, per-repository rules, Prometheus `/metrics`.
- **REST API** with personal API tokens, for scripts, CI jobs and bots. See
  [Using the API](#using-the-api).

## Using the API

Everything the UI does goes through the REST API under `/api/v1`. The live,
interactive reference is **Swagger UI** at `https://<eagle host>/docs` (also
`/redoc`, and the schema at `/openapi.json`): click **Authorize**, paste an API
token, and try any endpoint from the browser. More in
[deploy/README.md](deploy/README.md#using-the-api-and-swagger-ui-all-paths).

Scripts authenticate with a personal API token: **Account > API tokens >
Create token**. The token is shown once; only a hash is stored. It acts as you,
with your current role (a viewer's token can only read), and stops working when
it expires, is revoked, or your account is deactivated. Admins see and revoke
every token under **Admin > API tokens**. Creating and revoking tokens and
changing a password need a browser session, so a leaked token cannot create
more tokens or lock its owner out.

```bash
export EAGLE_URL=https://eagle-web-eagle.apps.example.com
export EAGLE_TOKEN=eagle_...            # from Account > API tokens

# Submit a PR (agent, model, post_comment and custom_prompt are optional)
curl -s -H "Authorization: Bearer $EAGLE_TOKEN" -H "Content-Type: application/json" \
  -d '{"pr_url": "https://github.com/org/repo/pull/12"}' \
  "$EAGLE_URL/api/v1/reviews"

# Follow it: status is queued, running, then succeeded, failed or cancelled
curl -s -H "Authorization: Bearer $EAGLE_TOKEN" "$EAGLE_URL/api/v1/reviews/<id>" \
  | jq '{status, verdict, summary}'
```

Other review calls: `GET /api/v1/reviews` (list and filter),
`POST /api/v1/reviews/<id>/cancel`, `POST /api/v1/reviews/<id>/rerun` and
`GET /api/v1/reviews/options` (agents and models you may pick).

## Deploy on OpenShift

You need a registry the cluster can pull from (for example a quay.io
organization) and, once, a cluster admin to add the catalog.

### 1. Publish the images

Either run the **release** GitHub Actions workflow (Actions > release > Run
workflow, with a version such as `0.1.0`), after setting the repository
variable `IMAGE_TAG_BASE` (for example `quay.io/hmoghani`) and the secrets
`REGISTRY_USERNAME` and `REGISTRY_PASSWORD`. Or build from a workstation:

```bash
export IMAGE_TAG_BASE=quay.io/hmoghani VERSION=0.1.0
make images images-push IMAGE=$IMAGE_TAG_BASE/eagle \
  RUNNER_IMAGE=$IMAGE_TAG_BASE/eagle-agent-runner TAG=v$VERSION
cd operator
make docker-build docker-push bundle bundle-build bundle-push \
  IMAGE_TAG_BASE=$IMAGE_TAG_BASE VERSION=$VERSION
make catalog catalog-build catalog-push IMAGE_TAG_BASE=$IMAGE_TAG_BASE VERSION=$VERSION  # macOS: add OPM_IN_CONTAINER=1
```

Images are built for `linux/amd64`. Five images come out:
`eagle`, `eagle-agent-runner`, `eagle-operator`, `eagle-operator-bundle` and
`eagle-operator-catalog`, all tagged `v<VERSION>`.

### 2. Add the catalog (cluster admin, once)

```yaml
apiVersion: operators.coreos.com/v1alpha1
kind: CatalogSource
metadata:
  name: eagle-catalog
  namespace: openshift-marketplace
spec:
  sourceType: grpc
  image: quay.io/hmoghani/eagle-operator-catalog:v0.1.0
  displayName: Eagle
  grpcPodConfig:
    securityContextConfig: restricted
```

### 3. Install from the web console

1. **Operators > OperatorHub**, search **Eagle**, **Install** (all namespaces,
   channel `alpha`).
2. Create a project, for example `eagle`, and optionally a Secret with the first
   admin's password: `oc create secret generic eagle-admin -n eagle --from-literal=password=...`
3. **Installed Operators > Eagle > Review Platform > Create**. Every field is
   optional; the form groups admin account, external access, database, review
   runners, single sign-on and advanced settings.
4. Wait for **Phase: Ready** and open the **URL** on the resource. Without an
   admin password Secret, read the generated one:
   `oc logs -n eagle deploy/eagle-web | grep -i password`

Command line and plain Kustomize installs, upgrades and every option:
[deploy/README.md](deploy/README.md). Operator reference:
[operator/README.md](operator/README.md).

### 4. Set up Eagle (admin pages)

1. **Credentials**: add one per model provider and press **Test**.
   - OpenAI-compatible endpoints need the version in the URL:
     `https://maas.example.com/v1`.
   - Anthropic-compatible endpoints (Claude Code through a MaaS) take the root
     URL without `/v1`.
2. **Agents**: pick each agent's credential, **Fetch models**, tick the models
   members may use and choose a default. Set a default for agents on an
   Anthropic-compatible or OpenAI-compatible gateway: without one the CLI asks
   the gateway for its own built-in model name.
3. **Git hosts**: a GitHub token for private repositories or to post comments.
4. **Notifications**, **Repositories** (per-repo instructions) and **Settings**
   (concurrency, PR poller, comment default) as needed.
5. **Users**: create accounts, or enable SSO in the ReviewPlatform.

## Where configuration lives

| What | Where |
|------|-------|
| Install shape: images, replicas, database, route, runner limits, SSO | the `ReviewPlatform` resource |
| Credentials, models, agents, users, repos, notifications | Eagle's database, edited in the admin pages. Secrets are encrypted |
| Encryption and session keys | Secret `<name>-keys`, generated once. **Back it up**: without it stored credentials cannot be decrypted |
| Database password | Secret `<name>-db` |
| The credential for one review | a short lived Secret owned by that review's Job |

Deleting the ReviewPlatform keeps `<name>-keys`, `<name>-db` and the database
volume, so reinstalling with the same name keeps all data.

## Operating

- **Upgrade**: publish a new catalog with `PREVIOUS_VERSION` set and point the
  CatalogSource at it. OLM upgrades the operator, and instances that use the
  default images roll to the new release. Migrations run in the web pods' init
  container; the worker waits for them.
- **Restart**: `oc rollout restart deploy/eagle-web` (or `eagle-worker`).
- **Backup**: the database and the `<name>-keys` Secret, together. Commands in
  [deploy/README.md](deploy/README.md#backup-and-restore).
- **Locked out**: `oc exec -n eagle deploy/eagle-web -- eagle reset-password admin`.
- **Status**: `oc get rp -n eagle`, conditions `Ready`, `DatabaseReady`, `Available`.
- **Review pods**: `oc get jobs -n eagle -l app.kubernetes.io/component=eagle-runner`;
  finished Jobs are kept for an hour for debugging.

## Security model

- Review pods run the agent with read-only tools, as non-root, with a read-only
  root filesystem, no service account token, and network egress that blocks
  private address ranges (configurable when a model endpoint is internal).
- Each review gets only its own credential, in a Secret deleted when the review
  finishes, and reports back with a one-time token.
- API tokens are stored as SHA-256 hashes, shown once, carry their owner's
  role, and cannot create tokens or change passwords. Creation and revocation
  are in the audit log.
- Secrets in the UI are write-only and masked; agent output is scrubbed of known
  secret values. Subscription credentials show a warning: sharing a personal
  subscription through a team service may conflict with provider terms.

## Cluster test (CI and local)

`operator/hack/kind-olm-test.sh` is the end to end test. On a kind cluster with
OLM it builds every image, installs the operator from a catalog the way
OperatorHub does, creates a ReviewPlatform, checks it is Ready and serving, then
runs a real review: the actual Claude Code CLI, in a review Job, reviews
`octocat/Hello-World#1` against a fake model API running in the cluster, and the
review must finish with a verdict and clean up its Secret.

CI runs it (`e2e` workflow) on changes to the operator, runner, backend code or
Containerfiles, and before every release. To run it locally:

```bash
operator/hack/kind-olm-test.sh            # podman (rootful machine on macOS); CONTAINER_TOOL=docker also works
operator/hack/kind-olm-test.sh --delete   # clean up
```

Needs kind, kubectl, Go and podman or docker.

## Development

Requirements: `uv`, Node 20+, git.

```bash
make setup                             # uv sync, npm install, pre-commit install
cp backend/.env.example backend/.env   # then edit it
make backend-dev                       # API on :8000 (docs at /docs), runs migrations
make mock-dev                          # worker with canned reviews (no model calls)
make frontend-dev                      # UI on http://localhost:5173
```

`make worker-dev` runs real agents as local processes instead (needs the CLIs on
PATH and a credential configured in the admin pages).

Checks, all run in CI:

```bash
make lint test test-frontend
cd operator && make test-envtest bundle
```

Tests never call real models or GitHub: agent CLIs are replaced by fake scripts
and GitHub, Slack, SMTP, the identity provider and model endpoints by in-process
fakes.

## Repository layout

| Path | Contents |
|------|----------|
| `backend/` | FastAPI API, worker, review runner, Alembic migrations, tests |
| `frontend/` | React and PatternFly UI |
| `agent-runner/` | Review pod image (git and the pinned agent CLIs) |
| `operator/` | Go operator, `ReviewPlatform` CRD, OLM bundle, kind test script |
| `deploy/` | Install guide and Kustomize manifests for installs without the operator |
| `.github/workflows/` | `ci` (backend, frontend, images), `operator` (tests, bundle check), `e2e` (kind cluster test), `release` (e2e, then publish images) |
| `PLAN.md` | Design decisions, data model and security model |

## Status

Tested in CI: unit and integration suites, the operator against a real
Kubernetes API server, and the full OperatorHub install plus a complete review
(real Claude Code CLI, fake model API) on a kind cluster with OLM. Operator
upgrades through OLM were tested by hand on kind.

Not yet verified: an OpenShift cluster (console form, Routes, random UIDs),
reviews against real models (the agent CLI flags are pinned in
`agent-runner/Containerfile` and should get one real review per agent), and SSO
against a real identity provider.

## License

Apache License 2.0, see [LICENSE](LICENSE).
