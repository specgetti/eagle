# Eagle operator

Go operator (controller-runtime, kubebuilder go/v4 layout) that reconciles a
`ReviewPlatform` custom resource (`eagle.redhat.com/v1alpha1`) into a running
Eagle installation on OpenShift.

For step by step install commands (operator path and plain Kustomize path) see
[../deploy/README.md](../deploy/README.md).

## What it creates

For a `ReviewPlatform` named `<name>` in namespace `<ns>`:

| Resource | Name | Owned by CR | Notes |
|----------|------|-------------|-------|
| Deployment | `<name>-web` | yes | default CMD (uvicorn :8000), `migrate` initContainer (`eagle migrate`), probes `/healthz` and `/readyz` |
| Deployment | `<name>-worker` | yes | `eagle worker` |
| Service | `<name>-web` | yes | port 8000 (`/metrics` too) |
| Route | `<name>-web` | yes | edge TLS, HTTP redirected; only when `spec.route.enabled` |
| ServiceAccount | `<name>-web` | yes | no permissions, token not mounted |
| ServiceAccount | `<name>-worker` | yes | bound to the worker Role |
| ServiceAccount | `<name>-runner` | yes | no RoleBinding, `automountServiceAccountToken: false` |
| Role + RoleBinding | `<name>-worker` | yes | jobs create/get/list/watch/delete; secrets create/get/delete/patch; pods get/list; pods/log get |
| NetworkPolicy | `<name>-runner` | yes | runner pods: no ingress; egress DNS, web:8000, `0.0.0.0/0` except `blockedCIDRs` |
| NetworkPolicy | `<name>-web` | yes | web ingress on 8000 from router, monitoring and same namespace |
| NetworkPolicy | `<name>-db` | yes | Postgres ingress on 5432 from web and worker only (managed DB) |
| StatefulSet + Service | `<name>-db` | yes | managed Postgres only |
| Secret | `<name>-database-url` | yes | `url` key, rebuilt from `<name>-db` (managed DB only) |
| Secret | `<name>-keys` | **no** | generated once unless `spec.secrets.existingSecret` |
| Secret | `<name>-db` | **no** | generated once (managed DB only) |
| PVC | `data-<name>-db-0` | **no** | created by the StatefulSet, retention policy `Retain` |

### What survives deleting the CR

Owned objects are garbage collected when the `ReviewPlatform` is deleted. Three
objects are intentionally left behind so a reinstall with the same name picks up
the existing data:

- `<name>-keys`: holds `EAGLE_ENCRYPTION_KEY`. Losing it makes every credential
  stored in the database unreadable.
- `<name>-db`: the Postgres password that matches the data on the volume.
- `data-<name>-db-0`: the Postgres data volume.

They carry the annotation `eagle.redhat.com/retain-on-delete: "true"` (Secrets)
and the labels `app.kubernetes.io/instance=<name>`. To remove everything for
good:

```bash
oc delete reviewplatform <name>
oc delete secret <name>-keys <name>-db
oc delete pvc data-<name>-db-0
```

The operator never updates `<name>-keys` or `<name>-db` after creating them. It
only ever issues a `Create`, checked against an uncached read, so it cannot
overwrite them. If you delete `<name>-keys` the operator generates a new one,
which is only safe on an empty database.

## Spec reference

| Field | Default | Description |
|-------|---------|-------------|
| `image` | operator default (`RELATED_IMAGE_EAGLE`) | App image (web and worker) |
| `imagePullPolicy` | cluster default | Applies to app and Postgres containers |
| `replicas` | `1` | Web replicas |
| `workerReplicas` | `1` | Worker replicas |
| `runner.image` | operator default (`RELATED_IMAGE_EAGLE_RUNNER`) | Agent runner image, passed to the worker as `EAGLE_RUNNER_IMAGE` |
| `runner.resources.limits.cpu` / `.memory` | `2` / `4Gi` | Passed as `EAGLE_RUNNER_CPU_LIMIT` / `EAGLE_RUNNER_MEMORY_LIMIT` |
| `runner.blockedCIDRs` | RFC 1918 ranges | IPv4 ranges runner pods cannot reach. Empty means the defaults |
| `route.enabled` | `true` | Create the Route |
| `route.host` | assigned by OpenShift | External host. Also used for `EAGLE_PUBLIC_URL` when the Route is disabled |
| `route.tls.termination` | `edge` | Only `edge` is supported (the app serves plain HTTP) |
| `route.tls.insecureEdgeTerminationPolicy` | `Redirect` | `Redirect`, `None` or `Allow` |
| `database.managed.enabled` | `true` | Run Postgres in a StatefulSet |
| `database.managed.image` | operator default (`RELATED_IMAGE_POSTGRESQL`, `registry.redhat.io/rhel9/postgresql-16:latest`) | Must accept `POSTGRESQL_USER/PASSWORD/DATABASE` |
| `database.managed.storageSize` | `5Gi` | Applied at creation only (claim templates are immutable) |
| `database.managed.storageClassName` | cluster default | Applied at creation only |
| `database.managed.resources` | none | Postgres container resources |
| `database.external.secretName` | | Secret with the full `postgresql+asyncpg://` URL |
| `database.external.key` | `url` | Key in that Secret |
| `secrets.existingSecret` | generate `<name>-keys` | Secret with `EAGLE_SECRET_KEY` and `EAGLE_ENCRYPTION_KEY` |
| `bootstrapAdmin.username` | `admin` | `EAGLE_BOOTSTRAP_ADMIN_USERNAME` |
| `bootstrapAdmin.passwordSecretRef` | none | `{name, key}` (key defaults to `password`). Without it the app logs a generated password once |
| `resources.web`, `resources.worker` | none | Container resources |
| `sso.issuer`, `sso.clientId` | none | OIDC provider and client. SSO env is emitted only when both are set |
| `sso.clientSecretRef` | none | `{name, key}` (key defaults to `clientSecret`) |
| `sso.scopes` | `openid email profile` | Space separated |
| `sso.buttonLabel`, `sso.usernameClaim`, `sso.groupsClaim` | app defaults | Omitted from env when empty |
| `sso.adminGroups`, `sso.memberGroups`, `sso.viewerGroups` | `[]` | Group to role mapping |
| `sso.defaultRole` | `viewer` | `admin`, `member`, `viewer` or `none` (deny) |
| `sso.localLoginEnabled` | `true` | Keep local username/password login (break glass) |

Every field is optional: an empty `spec` gives a working install with the
operator's default images, a bundled PostgreSQL, generated keys and a Route.

### Default images

The operator reads its default images from its own environment:
`RELATED_IMAGE_EAGLE`, `RELATED_IMAGE_EAGLE_RUNNER` and
`RELATED_IMAGE_POSTGRESQL`. They are written into `config/manager` by
`make set-images` (run by `make deploy` and `make bundle`) from the `VERSION`,
`IMAGE_TAG_BASE`, `APP_IMG`, `RUNNER_IMG` and `POSTGRES_IMG` Makefile variables.

Defaults are applied in memory on every reconcile and never written back to the
ReviewPlatform. So an operator upgrade moves every instance that leaves the
images empty to the new release, while instances that set an image stay pinned.
In the OLM bundle the same variables become the CSV's `relatedImages`, which is
what `oc adm catalog mirror` and similar tools use for disconnected clusters.

Exactly one database must be in effect: either managed (the default) or
`external` with `managed.enabled: false` (or `managed` omitted). This is enforced
by CEL rules in the CRD and again by the controller.

### Status

- `conditions`: `Ready`, `DatabaseReady`, `Available`
- `phase`: `Progressing`, `Ready`, or `Failed` (invalid spec, missing or
  incomplete user Secret)
- `url`: external URL (`https://<route host>`)
- `observedGeneration`

```bash
oc get reviewplatform            # short name: rp
oc describe rp eagle
```

## OLM bundle (OperatorHub)

`make bundle` generates `bundle/` (ClusterServiceVersion, CRD, metadata) from
`config/manifests` and validates it with `operator-sdk bundle validate
--select-optional suite=operatorframework`. The console form comes from the
`+operator-sdk:csv:customresourcedefinitions` markers in
`api/v1alpha1/reviewplatform_types.go`: display names, Secret and StorageClass
pickers, switches, and which fields sit under "Advanced". Edit those markers,
not the generated CSV.

| Target | What it does |
|--------|--------------|
| `make bundle` | Generate and validate `bundle/` for `VERSION` and `IMAGE_TAG_BASE` |
| `make bundle-build bundle-push` | Build and push `BUNDLE_IMG` |
| `make catalog` | Render a file based catalog for `BUNDLE_IMG` (needs it pushed). `PREVIOUS_VERSION=x.y.z` keeps that version and adds the upgrade edge. On macOS add `OPM_IN_CONTAINER=1` |
| `make catalog-build catalog-push` | Build and push `CATALOG_IMG` |
| `hack/kind-olm-test.sh` | Whole flow on a kind cluster with OLM: install from a catalog, instance Ready, then a complete review Job (Claude Code CLI against `hack/fake-anthropic.py`). Run by the `e2e` workflow; `CONTAINER_TOOL=docker` or `podman` |

`operator-sdk` and `opm` are downloaded into `bin/` at pinned versions
(`OPERATOR_SDK_VERSION`, `OPM_VERSION`). `catalog/` and `catalog.Dockerfile`
are build output and not committed; `bundle/` is.

## Day 2

- `oc rollout restart deploy/<name>-web` (or `-worker`) works: the operator
  keeps the restart annotation instead of rolling the pods back.
- On first install and on upgrades the worker waits for the web pods'
  `eagle migrate` init container to finish (it logs "Waiting for database
  migrations") instead of crash looping.

## Environment passed to the app

Web, migrate and worker: `EAGLE_DATABASE_URL`, `EAGLE_SECRET_KEY`,
`EAGLE_ENCRYPTION_KEY` (Secret refs), `EAGLE_BOOTSTRAP_ADMIN_USERNAME`,
`EAGLE_BOOTSTRAP_ADMIN_PASSWORD` (only with `passwordSecretRef`),
`EAGLE_AUTO_CREATE_SCHEMA=false`, `EAGLE_COOKIE_SECURE=true`, `EAGLE_PUBLIC_URL`
(omitted while no host is known).

Web container only (not migrate, not worker), from `spec.sso`:
`EAGLE_OIDC_ISSUER`, `EAGLE_OIDC_CLIENT_ID`, `EAGLE_OIDC_CLIENT_SECRET` (Secret
ref), `EAGLE_OIDC_SCOPES`, `EAGLE_OIDC_BUTTON_LABEL`, `EAGLE_OIDC_USERNAME_CLAIM`,
`EAGLE_OIDC_GROUPS_CLAIM`, `EAGLE_OIDC_ADMIN_GROUPS`, `EAGLE_OIDC_MEMBER_GROUPS`,
`EAGLE_OIDC_VIEWER_GROUPS` (JSON arrays such as `["eagle-admins","ops"]`),
`EAGLE_OIDC_DEFAULT_ROLE`. These are set only when `sso.issuer` and
`sso.clientId` are both present. `EAGLE_LOCAL_LOGIN_ENABLED` (`true`/`false`)
is set whenever `spec.sso` exists.

Worker only: `EAGLE_EXECUTOR=kubernetes`, `EAGLE_INSTANCE_NAME=<name>`,
`EAGLE_K8S_NAMESPACE` (downward API), `EAGLE_RUNNER_IMAGE`,
`EAGLE_RUNNER_SERVICE_ACCOUNT=<name>-runner`,
`EAGLE_CALLBACK_BASE_URL=http://<name>-web.<ns>.svc:8000`,
`EAGLE_RUNNER_CPU_LIMIT`, `EAGLE_RUNNER_MEMORY_LIMIT`.

The worker must label runner pods `app.kubernetes.io/component=eagle-runner` and
`app.kubernetes.io/instance=$EAGLE_INSTANCE_NAME`, otherwise the runner
NetworkPolicy does not apply to them.

## Single sign-on (OIDC)

1. Register a confidential OIDC client with your IdP (for example Red Hat SSO /
   Keycloak) with this redirect URI:

   ```
   <EAGLE_PUBLIC_URL>/api/v1/auth/oidc/callback
   ```

   `EAGLE_PUBLIC_URL` is `status.url` of the ReviewPlatform, for example
   `https://eagle.apps.example.com/api/v1/auth/oidc/callback`. Setting
   `spec.route.host` keeps it stable.
2. Store the client secret and reference it:

   ```bash
   oc create secret generic eagle-oidc -n eagle --from-literal=clientSecret='<client secret>'
   oc patch rp eagle -n eagle --type merge -p '{"spec":{"sso":{
     "issuer":"https://sso.example.com/realms/example","clientId":"eagle",
     "clientSecretRef":{"name":"eagle-oidc"},"groupsClaim":"groups",
     "adminGroups":["eagle-admins"],"defaultRole":"viewer"}}}'
   ```

Changing `spec.sso` rolls the web pods only.

## Design notes

- Each resource has a pure builder in `internal/controller/builders.go` (and
  `secrets.go`); `reviewplatform_controller.go` applies them with
  `controllerutil.CreateOrUpdate` and sets the controller reference.
- Immutable fields are not touched on update: StatefulSet `volumeClaimTemplates`,
  `selector` and `serviceName` are only set at creation, and a Route host
  assigned by OpenShift is preserved.
- The manager caches only Secrets labelled
  `app.kubernetes.io/managed-by=eagle-operator`. User Secrets (`existingSecret`,
  external DB, bootstrap password) are read with the uncached API reader.
- The runner NetworkPolicy allows ports 53 and 5353: OpenShift DNS pods listen
  on 5353 and NetworkPolicy is evaluated after the Service DNAT.
- The web NetworkPolicy also admits namespaces labelled
  `network.openshift.io/policy-group: monitoring` so Prometheus can scrape
  `/metrics`.
- On clusters without `route.openshift.io` (for example kind) the operator
  skips Routes and logs that at startup.
- The operator's ClusterRole includes `batch/jobs`, `pods` and `pods/log`
  because Kubernetes only lets it create the worker Role if it holds those
  permissions itself.

## Development

Requirements: Go 1.24+, and `podman` (or `docker`) for images.
`controller-gen`, `kustomize`, `setup-envtest`, `operator-sdk` and `opm` are
installed into `bin/` on demand, at pinned versions.

```bash
make generate manifests   # deepcopy, CRD and ClusterRole from markers
make test                 # unit + fake client reconcile tests
make test-envtest         # also runs against a real kube-apiserver/etcd (downloads binaries)
make build                # bin/manager
make run                  # run locally against the current kubeconfig (oc login first)
make verify               # CI: fail if generated files are stale
make bundle               # regenerate and validate bundle/ (commit the result)
hack/kind-olm-test.sh     # OperatorHub style install on a local kind cluster
```

Layout:

```
api/v1alpha1/             ReviewPlatform types, defaults, validation (+ tests)
cmd/main.go               manager entrypoint
internal/controller/      reconciler, builders, secret generation (+ tests)
config/crd/               generated CRD
config/rbac/              generated ClusterRole + bindings, leader election, metrics auth
config/manager/           operator Deployment; default images (default_images.yaml.in)
config/manifests/         base ClusterServiceVersion and icon for the OLM bundle
config/default/           kustomize entrypoint used by `make deploy`
config/samples/           example ReviewPlatforms (the first is the console form's default)
bundle/                   generated OLM bundle (make bundle)
hack/                     kind e2e script, fake Anthropic API, policy.json for opm in a container
```

## Known limitations

- `storageSize` and `storageClassName` changes after creation are ignored;
  expand the PVC manually if the storage class allows it.
- Managed Postgres is a single replica with no backups; use an external
  database for anything beyond a team instance, or back up the PVC.
- `blockedCIDRs` is IPv4 only. On dual stack clusters runner pods get no IPv6
  egress at all (no IPv6 rule is generated).
- Only `edge` TLS termination is supported.
