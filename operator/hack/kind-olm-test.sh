#!/usr/bin/env bash
# End to end test on a local kind cluster with OLM, before OpenShift. Used by CI
# (.github/workflows/e2e.yml) and on a laptop.
#
# 1. Builds every image for the local architecture into a local registry.
# 2. Installs OLM and the operator from a file based catalog through a
#    Subscription (what the console's Install button creates).
# 3. Creates a ReviewPlatform and waits for Ready.
# 4. Runs a real review: a fake Anthropic API runs in the cluster, an admin
#    credential points at it, and the real Claude Code CLI in a review Job
#    reviews octocat/Hello-World#1 and must produce a verdict.
#
#   operator/hack/kind-olm-test.sh            # set up and test
#   operator/hack/kind-olm-test.sh --delete   # remove the cluster and registry
#
# Environment:
#   CONTAINER_TOOL        podman (default, needs a rootful machine on macOS) or docker
#   EAGLE_E2E_GITHUB_TOKEN  optional GitHub token, avoids anonymous API rate limits (CI)
#   SKIP_REVIEW=1         stop after the install checks
#   PRUNE_AFTER_PUSH=1    delete local build output once images are in the registry
#                         (CI disk space; never set this on a machine you care about)
#   BUILDX_CACHE=gha      build the app, runner and operator images with docker buildx
#                         and this BuildKit cache backend, pushing straight to the
#                         registry (CI; the builder must reach localhost:5001 over http)
# Needs kind, kubectl, Go and curl. Uses context kind-eagle and registry localhost:5001.
set -euo pipefail

CLUSTER=eagle
REG_NAME=kind-registry
REG_PORT=5001
REG=localhost:${REG_PORT}
VERSION=${VERSION:-0.1.0}
OLM_VERSION=${OLM_VERSION:-v0.46.0}
CT=${CONTAINER_TOOL:-podman}
PLATFORM=linux/$(uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/')
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
OPERATOR=$ROOT/operator
KCTL="kubectl --context kind-${CLUSTER}"
MAKE_VARS=(IMAGE_TAG_BASE=$REG VERSION=$VERSION CONTAINER_TOOL=$CT)
PR_URL=https://github.com/octocat/Hello-World/pull/1
B=http://localhost:18000/api/v1
COOKIES=$(mktemp)

if [[ $CT == podman ]]; then export KIND_EXPERIMENTAL_PROVIDER=podman; fi

if [[ ${1:-} == "--delete" ]]; then
  kind delete cluster --name "$CLUSTER" || true
  $CT rm -f "$REG_NAME" || true
  exit 0
fi

step() { printf '\n==> %s\n' "$*"; }
push() {
  if [[ $CT == podman ]]; then podman push -q --tls-verify=false "$1"; else docker push -q "$1" >/dev/null; fi
}
api() { curl -sf -b "$COOKIES" -c "$COOKIES" -H 'Content-Type: application/json' "$@"; }
json() { python3 -c "import sys,json; d=json.load(sys.stdin); print($1)"; }
diagnose() {
  echo "---- diagnostics"
  df -h / /var/lib/docker 2>/dev/null | uniq || true
  echo "registry: $(curl -s "http://$REG/v2/_catalog")"
  for image in eagle eagle-agent-runner eagle-operator eagle-operator-bundle; do
    echo "  $image tags: $(curl -s "http://$REG/v2/$image/tags/list")"
  done
  $KCTL get csv,installplan,sub -n operators -o wide || true
  $KCTL describe pods -n operators | sed -n '/^Name:/p;/Image:/p;/^Events:/,/^$/p' || true
  $KCTL get events -A --sort-by=.lastTimestamp | grep -iE "pull|image|fail|error|backoff" | tail -20 || true
  $KCTL get rp,deploy,pods,jobs -n eagle -o wide || true
  $KCTL logs -n eagle deploy/eagle-worker --tail=40 || true
  $KCTL logs -n eagle -l app.kubernetes.io/component=eagle-runner --tail=40 || true
  $KCTL logs -n eagle deploy/fake-llm --tail=20 || true
  $KCTL logs -n operators deploy/eagle-operator-controller-manager --tail=20 || true
}
PF=""
cleanup() {
  [[ -n $PF ]] && kill "$PF" 2>/dev/null || true
  rm -f "$COOKIES"
}
trap cleanup EXIT
fail() { echo "FAIL: $*"; diagnose; exit 1; }

step "Local registry on $REG ($CT)"
if ! $CT container inspect "$REG_NAME" >/dev/null 2>&1; then
  $CT run -d --restart=always -p "127.0.0.1:${REG_PORT}:5000" --name "$REG_NAME" docker.io/library/registry:2 >/dev/null
fi

step "kind cluster '$CLUSTER'"
if ! kind get clusters 2>/dev/null | grep -qx "$CLUSTER"; then
  kind create cluster --name "$CLUSTER" --wait 120s --config - <<'EOF'
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
containerdConfigPatches:
  - |-
    [plugins."io.containerd.grpc.v1.cri".registry]
      config_path = "/etc/containerd/certs.d"
EOF
fi
$CT network connect kind "$REG_NAME" 2>/dev/null || true
$CT exec "${CLUSTER}-control-plane" sh -c "mkdir -p /etc/containerd/certs.d/${REG} && \
  printf '[host.\"http://${REG_NAME}:5000\"]\n' > /etc/containerd/certs.d/${REG}/hosts.toml"

if [[ -n ${BUILDX_CACHE:-} ]]; then
  # CI: BuildKit with a layer cache, pushing straight to the local registry (the
  # builder needs host networking and http for $REG; see .github/workflows/e2e.yml).
  build() {
    local log start=$SECONDS
    log=$(mktemp)
    if ! docker buildx build --platform "$PLATFORM" --provenance=false --push --progress=plain \
      --cache-from "type=$BUILDX_CACHE,scope=$1" --cache-to "type=$BUILDX_CACHE,mode=max,scope=$1" \
      -t "$REG/$1:v$VERSION" -f "$3" "$2" >"$log" 2>&1; then
      tail -60 "$log"
      rm -f "$log"
      return 1
    fi
    echo "$1: $((SECONDS - start))s, $(grep -c ' CACHED$' "$log" || true) cached steps"
    rm -f "$log"
  }
else
  build() {
    $CT build -q --platform "$PLATFORM" -t "$REG/$1:v$VERSION" -f "$3" "$2" >/dev/null
    push "$REG/$1:v$VERSION"
  }
fi

step "Build and push images ($PLATFORM)"
build eagle "$ROOT" "$ROOT/Containerfile"
build eagle-agent-runner "$ROOT" "$ROOT/agent-runner/Containerfile"
build eagle-operator "$OPERATOR" "$OPERATOR/Dockerfile"

step "Bundle and catalog"
make -C "$OPERATOR" bundle "${MAKE_VARS[@]}" | tail -1
make -C "$OPERATOR" bundle-build "${MAKE_VARS[@]}" >/dev/null
push "$REG/eagle-operator-bundle:v$VERSION"
make -C "$OPERATOR" catalog "${MAKE_VARS[@]}" OPM_IN_CONTAINER=1 OPM_RENDER_FLAGS=--use-http >/dev/null
make -C "$OPERATOR" catalog-build "${MAKE_VARS[@]}" >/dev/null
push "$REG/eagle-operator-catalog:v$VERSION"

if [[ ${PRUNE_AFTER_PUSH:-} == 1 ]]; then
  step "Free disk space (images are in the registry now)"
  for image in eagle eagle-agent-runner eagle-operator eagle-operator-bundle eagle-operator-catalog; do
    $CT rmi -f "$REG/$image:v$VERSION" >/dev/null 2>&1 || true
  done
  if [[ $CT == docker ]]; then docker builder prune -af >/dev/null; else podman image prune -f >/dev/null; fi
  # The BuildKit builder keeps its own cache (already exported to the CI cache).
  if [[ -n ${BUILDX_CACHE:-} ]]; then docker buildx prune -af >/dev/null; fi
  df -h / | tail -1
fi

step "OLM $OLM_VERSION"
if ! $KCTL get crd clusterserviceversions.operators.coreos.com >/dev/null 2>&1; then
  "$OPERATOR/bin/operator-sdk" olm install --version "$OLM_VERSION" --timeout 5m >/dev/null 2>&1
fi

step "Install the operator through a CatalogSource and Subscription"
$KCTL apply -f - >/dev/null <<EOF
apiVersion: operators.coreos.com/v1alpha1
kind: CatalogSource
metadata: {name: eagle-catalog, namespace: olm}
spec:
  sourceType: grpc
  image: $REG/eagle-operator-catalog:v$VERSION
  displayName: Eagle (local test)
  grpcPodConfig: {securityContextConfig: restricted}
---
apiVersion: operators.coreos.com/v1alpha1
kind: Subscription
metadata: {name: eagle-operator, namespace: operators}
spec:
  channel: alpha
  name: eagle-operator
  source: eagle-catalog
  sourceNamespace: olm
  installPlanApproval: Automatic
EOF
for _ in $(seq 1 60); do
  [[ $($KCTL get csv -n operators "eagle-operator.v$VERSION" -o jsonpath='{.status.phase}' 2>/dev/null) == Succeeded ]] && break
  sleep 5
done
[[ $($KCTL get csv -n operators "eagle-operator.v$VERSION" -o jsonpath='{.status.phase}' 2>/dev/null) == Succeeded ]] ||
  fail "operator CSV did not reach Succeeded"
$KCTL get csv -n operators "eagle-operator.v$VERSION"

step "Create a ReviewPlatform (operator default images)"
$KCTL apply -f - >/dev/null <<'EOF'
apiVersion: v1
kind: Namespace
metadata: {name: eagle}
---
apiVersion: v1
kind: Secret
metadata: {name: eagle-admin, namespace: eagle}
stringData: {password: kind-test-password}
---
apiVersion: eagle.redhat.com/v1alpha1
kind: ReviewPlatform
metadata: {name: eagle, namespace: eagle}
spec:
  bootstrapAdmin:
    passwordSecretRef: {name: eagle-admin}
  runner:
    # The fake model API runs inside the cluster (a private range). Block only a
    # documentation range so review pods can reach it; real installs keep the
    # RFC 1918 defaults.
    blockedCIDRs: ["192.0.2.0/24"]
  database:
    managed:
      # kind cannot pull registry.redhat.io without a pull secret; this is the
      # public CentOS Stream build of the same image (same environment contract).
      image: quay.io/sclorg/postgresql-16-c9s:latest
EOF
for _ in $(seq 1 72); do
  [[ $($KCTL get rp eagle -n eagle -o jsonpath='{.status.phase}' 2>/dev/null) == Ready ]] && break
  sleep 5
done
$KCTL get rp,pods -n eagle
[[ $($KCTL get rp eagle -n eagle -o jsonpath='{.status.phase}') == Ready ]] || fail "ReviewPlatform did not become Ready"

step "Smoke test through a port-forward"
$KCTL port-forward -n eagle svc/eagle-web 18000:8000 >/dev/null 2>&1 &
PF=$!
for _ in $(seq 1 15); do curl -sf localhost:18000/healthz >/dev/null && break; sleep 1; done
curl -sf localhost:18000/readyz || fail "readyz"
echo
curl -sf -o /dev/null -w 'UI: HTTP %{http_code}\n' localhost:18000/ || fail "UI not served"
api -X POST $B/auth/login -d '{"username":"admin","password":"kind-test-password"}' -o /dev/null -w 'admin login: HTTP %{http_code}\n' ||
  fail "admin login"
restarts=$($KCTL get pods -n eagle -l app.kubernetes.io/component=eagle-worker -o jsonpath='{.items[0].status.containerStatuses[0].restartCount}')
echo "worker restarts: $restarts"
[[ $restarts == 0 ]] || fail "worker restarted $restarts times"

if [[ ${SKIP_REVIEW:-} != 1 ]]; then
  step "Fake model API in the cluster"
  $KCTL create configmap fake-llm -n eagle --from-file=fake-anthropic.py="$OPERATOR/hack/fake-anthropic.py" \
    --dry-run=client -o yaml | $KCTL apply -f - >/dev/null
  $KCTL apply -f - >/dev/null <<EOF
apiVersion: apps/v1
kind: Deployment
metadata: {name: fake-llm, namespace: eagle}
spec:
  selector: {matchLabels: {app: fake-llm}}
  template:
    metadata: {labels: {app: fake-llm}}
    spec:
      automountServiceAccountToken: false
      securityContext: {runAsNonRoot: true, seccompProfile: {type: RuntimeDefault}}
      containers:
        - name: fake-llm
          image: $REG/eagle-agent-runner:v$VERSION
          command: [python3, /opt/fake/fake-anthropic.py, "8080"]
          ports: [{containerPort: 8080}]
          readinessProbe: {httpGet: {path: /healthz, port: 8080}}
          securityContext: {allowPrivilegeEscalation: false, capabilities: {drop: [ALL]}}
          volumeMounts: [{name: code, mountPath: /opt/fake}]
      volumes: [{name: code, configMap: {name: fake-llm}}]
---
apiVersion: v1
kind: Service
metadata: {name: fake-llm, namespace: eagle}
spec:
  selector: {app: fake-llm}
  ports: [{port: 8080, targetPort: 8080}]
EOF
  $KCTL rollout status -n eagle deploy/fake-llm --timeout=180s >/dev/null || fail "fake model API did not start"

  step "Configure Eagle through its API"
  if [[ -n ${EAGLE_E2E_GITHUB_TOKEN:-} ]]; then
    api -X POST $B/git-hosts -d "{\"host\":\"github.com\",\"token\":\"$EAGLE_E2E_GITHUB_TOKEN\"}" >/dev/null ||
      fail "add GitHub token"
    echo "GitHub token added"
  fi
  cred=$(api -X POST $B/credentials -d '{"name":"fake-llm","kind":"anthropic_compatible",
    "values":{"base_url":"http://fake-llm.eagle.svc:8080","api_key":"fake-key"}}' | json 'd["id"]') ||
    fail "create credential"
  models=$(api $B/credentials/$cred/models | json '",".join(d["models"])') || fail "fetch models"
  echo "Fetch models: $models"
  [[ $models == fake-model ]] || fail "unexpected models: $models"
  tested=$(api -X POST $B/credentials/$cred/test -d '{"model":"fake-model"}' | json 'd["ok"]') || fail "test credential"
  echo "Test credential: ok=$tested"
  [[ $tested == True ]] || fail "credential test did not pass"
  api -X PUT $B/agents/claude -d "{\"credential_id\":$cred,\"allowed_models\":[\"fake-model\"],\"default_model\":\"fake-model\"}" >/dev/null ||
    fail "configure claude agent"

  step "Review $PR_URL with the real Claude Code CLI in a review Job"
  rid=$(api -X POST $B/reviews -d "{\"pr_url\":\"$PR_URL\",\"agent\":\"claude\"}" | json 'd["id"]') || fail "submit review"
  status=queued
  for i in $(seq 1 60); do
    status=$(api $B/reviews/$rid | json 'd["status"]')
    case $status in succeeded | failed | cancelled) break ;; esac
    sleep 5
  done
  api $B/reviews/$rid | json '"status=%s verdict=%s head=%s\nsummary=%s\nerror=%s" % (d["status"], d["verdict"], (d["head_sha"] or "")[:12], d["summary"], d["error"])'
  [[ $status == succeeded ]] || fail "review ended as $status"
  [[ $(api $B/reviews/$rid | json 'd["verdict"]') == approve ]] || fail "review has no verdict"
  for _ in $(seq 1 12); do
    left=$($KCTL get secrets -n eagle -l app.kubernetes.io/component=eagle-runner -o name | wc -l | tr -d ' ')
    [[ $left == 0 ]] && break
    sleep 5
  done
  echo "review Secrets left: $left"
  [[ $left == 0 ]] || fail "the review's credential Secret was not deleted"
fi

step "Restore the release bundle (placeholder images, not $REG)"
make -C "$OPERATOR" bundle >/dev/null
rm -rf "$OPERATOR/catalog" "$OPERATOR/catalog.Dockerfile"

printf '\nOK. Use it with:\n  %s port-forward -n eagle svc/eagle-web 18000:8000\n  open http://localhost:18000 (admin / kind-test-password)\nRemove with: %s --delete\n' "$KCTL" "$0"
