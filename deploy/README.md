# Deploying Eagle on OpenShift

Three supported paths that produce the same resources:

1. **OperatorHub** (recommended): publish the operator in a catalog once, then
   anyone with project admin installs and configures Eagle from the OpenShift
   web console, using a form.
2. **Operator from the command line**: `make install deploy`, then create a
   `ReviewPlatform` with `oc apply`. Same operator, no catalog.
3. **Plain Kustomize** (this directory): apply the manifests yourself with
   `oc apply -k`. You create the Secrets by hand. Only needs project admin.

All paths need the two Eagle images pushed to a registry the cluster can pull from:

```bash
# From the repo root. Replace quay.io/hmoghani with your registry.
# --platform matters when building on an Apple silicon (arm64) Mac for an
# x86_64 cluster; without it the pods fail with "exec format error".
podman build --platform linux/amd64 -t quay.io/hmoghani/eagle:v0.1.0 -f Containerfile .
podman push quay.io/hmoghani/eagle:v0.1.0

# Agent runner image. The build context is the repo root (it copies backend/eagle/runner).
podman build --platform linux/amd64 -t quay.io/hmoghani/eagle-agent-runner:v0.1.0 \
  -f agent-runner/Containerfile .
podman push quay.io/hmoghani/eagle-agent-runner:v0.1.0
```

The bundled database uses `registry.redhat.io/rhel9/postgresql-16`, which
OpenShift pulls with the cluster pull secret.

---

## Path 1: OperatorHub (web console)

### 1. Build and push the images, bundle and catalog

`IMAGE_TAG_BASE` is your registry and organization; `VERSION` is the release.
The operator uses `<IMAGE_TAG_BASE>/eagle:v<VERSION>` and
`<IMAGE_TAG_BASE>/eagle-agent-runner:v<VERSION>` as its default images, so the
console form does not ask for image names.

```bash
export IMAGE_TAG_BASE=quay.io/hmoghani VERSION=0.1.0
make images images-push IMAGE=$IMAGE_TAG_BASE/eagle \
  RUNNER_IMAGE=$IMAGE_TAG_BASE/eagle-agent-runner TAG=v$VERSION

cd operator
make docker-build docker-push IMG=$IMAGE_TAG_BASE/eagle-operator:v$VERSION
make bundle bundle-build bundle-push IMAGE_TAG_BASE=$IMAGE_TAG_BASE VERSION=$VERSION
# On macOS add OPM_IN_CONTAINER=1. For an upgrade add PREVIOUS_VERSION=<old version>.
make catalog catalog-build catalog-push IMAGE_TAG_BASE=$IMAGE_TAG_BASE VERSION=$VERSION
```

All images must be public, or the cluster needs a pull secret for them.

### 2. Add the catalog to OperatorHub (cluster admin, once)

```yaml
# oc apply -f catalogsource.yaml
apiVersion: operators.coreos.com/v1alpha1
kind: CatalogSource
metadata:
  name: eagle-catalog
  namespace: openshift-marketplace
spec:
  sourceType: grpc
  image: quay.io/hmoghani/eagle-operator-catalog:v0.1.0
  displayName: Eagle
  publisher: hmoghani
  grpcPodConfig:
    securityContextConfig: restricted
  updateStrategy:
    registryPoll:
      interval: 30m
```

For a new release, build the catalog with `PREVIOUS_VERSION` set (so it keeps
the old version and adds the upgrade edge), push it, and change `spec.image`.
Or push to a moving tag such as `:latest` and let `registryPoll` pick it up.

### 3. Install from the console

1. **Operators > OperatorHub**, search for **Eagle**, click **Install**. Keep
   "All namespaces" and the `alpha` channel. **Installed Namespace** is where
   the operator pod runs (`openshift-operators` by default), not Eagle itself.
   Automatic update approval upgrades the operator when the catalog gets a new
   version; instances that use the default images then roll to the new
   release on their own.
2. Create a project for Eagle, for example `eagle`.
3. Switch the console's **Project** selector to that project, then **Operators >
   Installed Operators > Eagle > Review Platform > Create**. The form creates
   the instance in the selected project, so with the operator's project still
   selected Eagle lands next to the operator. Any Secret the form refers to
   (such as the admin password) must be in the same project. The form groups
   the settings; every field is optional:
   - **First admin account**: pick a Secret with the admin password, or leave
     empty and read the generated password from the `eagle-web` pod log.
   - **External access**: set a fixed host name if you plan to enable SSO.
   - **Database**: built-in PostgreSQL (size, storage class) or an external one.
   - **Review runners**: CPU and memory per review, and the blocked network
     ranges. If your model endpoint (for example an internal MaaS) has a
     private address, remove its range here.
   - **Single sign-on**: issuer, client ID, client secret Secret, group mapping.
   - **Advanced**: image overrides, an existing encryption key Secret (restore).
4. Wait for **Phase: Ready**, then open the **URL** shown on the resource.

Credentials, models, agents, GitHub tokens, notifications and users are then
configured in Eagle's own admin pages, not in this form.

### Troubleshooting the install

- **Install spins with no progress, the Subscription has no status.** OLM
  resolves every Subscription in a namespace together. If `openshift-operators`
  already holds an InstallPlan waiting for manual approval (another operator
  set to Manual), no new plan is made there, Eagle's included. Check with
  `oc get installplan -n openshift-operators`. Rather than approving someone
  else's upgrades, install Eagle's operator into its own namespace:

  ```bash
  oc delete subscription eagle-operator -n openshift-operators --ignore-not-found
  oc create namespace eagle-operator
  oc apply -f - <<'EOF'
  apiVersion: operators.coreos.com/v1
  kind: OperatorGroup
  metadata:
    name: eagle-operator
    namespace: eagle-operator
  spec: {}            # no target namespaces: watches all namespaces
  ---
  apiVersion: operators.coreos.com/v1alpha1
  kind: Subscription
  metadata:
    name: eagle-operator
    namespace: eagle-operator
  spec:
    channel: alpha
    name: eagle-operator
    source: eagle-catalog
    sourceNamespace: openshift-marketplace
    installPlanApproval: Automatic
  EOF
  oc get csv -n eagle-operator -w     # wait for eagle-operator.v... Succeeded
  ```

  `oc get csv -n eagle-operator` also lists the other cluster wide operators:
  OLM copies their CSVs into every namespace for display. Only Eagle runs there.
- **Pods stop with `CreateContainerConfigError`, "secret ... not found".** The
  ReviewPlatform is in a different project than the Secret it refers to (see
  step 3). Delete it and create it in the Secret's project; the operator keeps
  `eagle-keys`, `eagle-db`, `eagle-database-url` and the `data-eagle-db-0` PVC
  on purpose, so delete those too if the misplaced instance has no data.
- **A review pod's log.** `oc logs -n eagle -f -l eagle.redhat.com/review-id=<id>`
  (or `-l app.kubernetes.io/component=eagle-runner` for all of them) shows the checkout, the agent and model, every tool the agent calls and the
  verdict as the review runs. The worker logs one line per finished review:
  `oc logs -n eagle deploy/eagle-worker | grep Review`.

### Test locally first

`operator/hack/kind-olm-test.sh` runs the same install on a local kind cluster
with OLM (no OpenShift needed): it builds every image, publishes a catalog,
installs through a Subscription, creates a ReviewPlatform, checks that it is
Ready and serving, and runs a complete review with the Claude Code CLI against
a fake model API. CI runs it too (`e2e` workflow, also before each release).
`--delete` removes the cluster and the local registry.

---

## Path 2: operator from the command line

### 1. Build and push the operator image

```bash
cd operator
export IMAGE_TAG_BASE=quay.io/hmoghani VERSION=0.1.0
make docker-build docker-push IMAGE_TAG_BASE=$IMAGE_TAG_BASE VERSION=$VERSION
```

### 2. Install the CRD and the operator (cluster admin)

```bash
oc login ...                      # as cluster admin
make install deploy IMAGE_TAG_BASE=$IMAGE_TAG_BASE VERSION=$VERSION
oc -n eagle-operator-system rollout status deploy/eagle-operator-controller-manager
```

`make install` applies the CRD; `make deploy` creates the
`eagle-operator-system` namespace, RBAC and the operator Deployment. It also
writes the operator image and its default app, runner and PostgreSQL images
(`<IMAGE_TAG_BASE>/eagle:v<VERSION>` and so on) into `config/manager`. Both download
`kustomize` into `operator/bin` with Go; to use an existing binary instead pass
`KUSTOMIZE=$(which kustomize)`.

### 3. Create a ReviewPlatform

```bash
oc new-project eagle
oc apply -n eagle -f config/samples/eagle_v1alpha1_reviewplatform.yaml
oc get rp -n eagle -w             # wait for PHASE=Ready
oc get rp eagle -n eagle -o jsonpath='{.status.url}{"\n"}'
```

If you did not set `spec.bootstrapAdmin.passwordSecretRef`, read the generated
admin password from the web log on first start:

```bash
oc logs -n eagle deploy/eagle-web | grep -i password
```

See `operator/config/samples/eagle_v1alpha1_reviewplatform_full.yaml` for every
field (external database, custom host, bootstrap password, resources) and
[../operator/README.md](../operator/README.md) for the full reference.

### Upgrade

Deploy the new operator version (`make deploy` with the new `VERSION`).
Instances that leave the images empty move to the new release's default images
on their own; instances that pin `spec.image` / `spec.runner.image` move when
you change those fields. The web pods run `eagle migrate` as an initContainer
before starting, so schema migrations are applied on rollout, and the worker
waits for them instead of failing. `oc rollout restart` works on the web and
worker Deployments; the operator keeps the restart.

```bash
oc patch rp eagle -n eagle --type merge -p '{"spec":{"image":"quay.io/hmoghani/eagle:v0.2.0"}}'
```

### Uninstall

```bash
oc delete rp eagle -n eagle
# Retained on purpose (encryption key, DB password, DB volume). Delete only if
# you want to lose all data:
oc delete secret -n eagle eagle-keys eagle-db
oc delete pvc -n eagle data-eagle-db-0

cd operator && make undeploy uninstall
```

---

## Path 3: plain Kustomize (no operator)

The manifests here are the equivalent of a `ReviewPlatform` named `eagle` with
the managed database, deployed into the namespace `eagle`.

| File | Contents |
|------|----------|
| `kustomization.yaml` | namespace, image tag, `eagle-config` ConfigMap generator |
| `config.env` | public URL, bootstrap admin username, runner image and limits |
| `serviceaccounts.yaml` | `eagle-web`, `eagle-worker`, `eagle-runner` |
| `rbac.yaml` | worker Role and RoleBinding |
| `postgres.yaml` | Postgres Service and StatefulSet (PVC retained) |
| `web.yaml` | web Deployment (with `migrate` initContainer) and Service |
| `worker.yaml` | worker Deployment |
| `route.yaml` | edge TLS Route |
| `networkpolicies.yaml` | runner, web and Postgres NetworkPolicies |
| `sso.env` | optional OIDC SSO settings (disabled until uncommented) |
| `secrets.example.yaml` | reference for the Secrets; **not** applied by `-k` |

### 1. Create the project

```bash
oc new-project eagle
```

To use another namespace, run `kustomize edit set namespace <ns>` in this
directory (or change `namespace:` in `kustomization.yaml`) and use `-n <ns>`
below. Everything else, including the RoleBinding subject, follows.

### 2. Create the Secrets (once)

Generate the values in the shell so they never land in a file. **Never
recreate `eagle-keys` later**: `EAGLE_ENCRYPTION_KEY` encrypts the stored
credentials, and replacing it makes them unreadable.

```bash
# App keys. EAGLE_ENCRYPTION_KEY is a Fernet key: 32 random bytes, URL safe base64.
oc create secret generic eagle-keys -n eagle \
  --from-literal=EAGLE_SECRET_KEY="$(openssl rand -base64 48 | tr '+/' '-_' | tr -d '=\n')" \
  --from-literal=EAGLE_ENCRYPTION_KEY="$(openssl rand -base64 32 | tr '+/' '-_')"

# Bundled Postgres credentials and the matching URL. Hex keeps the password URL safe.
DB_PASSWORD="$(openssl rand -hex 24)"
oc create secret generic eagle-db -n eagle \
  --from-literal=POSTGRESQL_USER=eagle \
  --from-literal=POSTGRESQL_PASSWORD="$DB_PASSWORD" \
  --from-literal=POSTGRESQL_DATABASE=eagle
oc create secret generic eagle-database-url -n eagle \
  --from-literal=url="postgresql+asyncpg://eagle:${DB_PASSWORD}@eagle-db:5432/eagle"
unset DB_PASSWORD

# Optional: bootstrap admin password. Without it the app logs a generated one.
oc create secret generic eagle-bootstrap-admin -n eagle \
  --from-literal=password="$(openssl rand -base64 18)"
```

For an **external database**, create only `eagle-database-url` pointing at it,
then delete `postgres.yaml` from `resources:` and the `eagle-db` policy from
`networkpolicies.yaml` (or patch them out in an overlay).

### 3. Configure

Edit `config.env`:

- `EAGLE_PUBLIC_URL`: `https://<route host>`. Either set `spec.host` in
  `route.yaml` to a host under your apps domain and use the same value here,
  or apply once, read the assigned host and apply again:
  `oc get route eagle-web -n eagle -o jsonpath='{.spec.host}'`
- `EAGLE_RUNNER_IMAGE`, `EAGLE_RUNNER_CPU_LIMIT`, `EAGLE_RUNNER_MEMORY_LIMIT`

Set the app image tag (or edit `images:` in `kustomization.yaml`):

```bash
cd deploy && kustomize edit set image quay.io/hmoghani/eagle=quay.io/hmoghani/eagle:v0.1.0 && cd ..
```

### Optional: OIDC single sign-on

SSO settings go to the web container only.

1. Register a confidential client with your IdP using the redirect URI
   `<EAGLE_PUBLIC_URL>/api/v1/auth/oidc/callback`, for example
   `https://eagle.apps.example.com/api/v1/auth/oidc/callback`.
2. Create the client secret:

   ```bash
   oc create secret generic eagle-oidc -n eagle \
     --from-literal=EAGLE_OIDC_CLIENT_SECRET='<client secret>'
   ```

3. Edit `sso.env` (group lists must be JSON arrays, for example
   `["eagle-admins","ops"]`), then uncomment the `eagle-sso` entry under
   `configMapGenerator` in `kustomization.yaml` and the `envFrom` block on the
   `web` container in `web.yaml`.

With the operator, use `spec.sso` instead (see
[../operator/README.md](../operator/README.md#single-sign-on-oidc)).

### 4. Apply

```bash
oc kustomize deploy/                 # review the output first
oc apply -k deploy/
oc -n eagle rollout status statefulset/eagle-db
oc -n eagle rollout status deploy/eagle-web
oc -n eagle rollout status deploy/eagle-worker
oc -n eagle get route eagle-web
```

Changing `config.env` and re-applying rolls the web and worker pods, because
the generated ConfigMap name includes a content hash.

### Uninstall

```bash
oc delete -k deploy/
# The PVC and the Secrets you created are not part of the kustomization:
oc delete pvc -n eagle data-eagle-db-0
oc delete secret -n eagle eagle-keys eagle-db eagle-database-url eagle-bootstrap-admin eagle-oidc
```

---

## Using the API and Swagger UI (all paths)

Eagle serves its REST API under `/api/v1` on the same Route as the UI, with a
live, interactive reference:

| URL | What |
|-----|------|
| `https://<eagle host>/docs` | Swagger UI: every endpoint, its fields, and **Try it out** |
| `https://<eagle host>/redoc` | The same reference as a single readable page |
| `https://<eagle host>/openapi.json` | OpenAPI 3 schema, for generating a client |

The host is the ReviewPlatform's URL: `oc get rp eagle -n eagle -o jsonpath='{.status.url}'`.

To call the API, create a personal token in the UI under **Account > API
tokens** (shown once; it acts as you, with your role). Then either:

- **In Swagger UI**: click **Authorize**, paste the token under "API token",
  and use **Try it out** on any endpoint, for example `POST /api/v1/reviews`
  with `{"pr_url": "https://github.com/org/repo/pull/12"}`. When you are signed
  in to Eagle in the same browser, Swagger also works without a token.
- **From a script or CI job**:

  ```bash
  EAGLE_URL=$(oc get rp eagle -n eagle -o jsonpath='{.status.url}')
  curl -s -H "Authorization: Bearer $EAGLE_TOKEN" -H "Content-Type: application/json" \
    -d '{"pr_url": "https://github.com/org/repo/pull/12"}' "$EAGLE_URL/api/v1/reviews"
  curl -s -H "Authorization: Bearer $EAGLE_TOKEN" "$EAGLE_URL/api/v1/reviews/<id>"
  ```

Admins list and revoke every user's tokens under **Admin > API tokens**.
Creating or revoking tokens and changing a password need a signed in browser
session; a token cannot do it. See [../README.md](../README.md#using-the-api).

## Backup and restore

Two things make up an Eagle install's state: the PostgreSQL database and the
`<name>-keys` Secret. The key decrypts every credential stored in the database,
so a database backup without the matching key is only partly useful. Commands
below assume the instance is called `eagle` in project `eagle` with the bundled
database; for an external database use your provider's backups.

```bash
# Key (once, and after a restore from elsewhere). Store it like a password.
oc get secret eagle-keys -n eagle -o yaml > eagle-keys.yaml

# Database
oc exec -n eagle eagle-db-0 -- bash -c 'pg_dump -Fc -d "$POSTGRESQL_DATABASE"' > eagle.dump
```

Restore into a new or existing install:

```bash
# 1. Before creating the ReviewPlatform, recreate the key Secret (drop
#    metadata.uid, resourceVersion and ownerReferences from the saved yaml).
oc apply -n eagle -f eagle-keys.yaml
# 2. Create the ReviewPlatform as usual, then stop web and worker.
oc patch rp eagle -n eagle --type merge -p '{"spec":{"replicas":0,"workerReplicas":0}}'
# 3. Load the dump.
oc exec -i -n eagle eagle-db-0 -- bash -c \
  'pg_restore --clean --if-exists --no-owner -d "$POSTGRESQL_DATABASE"' < eagle.dump
# 4. Start them again (the web pods migrate the schema if the dump is older).
oc patch rp eagle -n eagle --type merge -p '{"spec":{"replicas":1,"workerReplicas":1}}'
```

Scale through the ReviewPlatform, not `oc scale`: the operator owns the
Deployments' replica counts.

## Security notes (all paths)

- Review Job pods run as `eagle-runner`, which has no RoleBinding and no
  mounted token. The worker labels them `app.kubernetes.io/component=eagle-runner`
  and `app.kubernetes.io/instance=<name>` (from `EAGLE_INSTANCE_NAME`) so the
  runner NetworkPolicy applies.
- The runner NetworkPolicy denies all ingress and allows egress only to DNS,
  the web pods on 8000 (result callback), and public IPv4 addresses (private
  ranges `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16` are blocked, which
  covers the default OpenShift pod, service and node networks). If your
  cluster uses other internal ranges, add them to `blockedCIDRs`.
- The worker Role allows: Jobs create/get/list/watch/delete, Secrets
  create/get/delete/patch, Pods get/list, Pods/log get, in its own namespace only.
- NetworkPolicies only take effect with a CNI that enforces them (OVN-Kubernetes
  and OpenShift SDN both do).
