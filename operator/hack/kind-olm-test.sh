#!/usr/bin/env bash
# End to end OperatorHub style install on a local kind cluster, before OpenShift.
#
# Builds every image for the local architecture, pushes them to a local registry,
# installs OLM, installs the operator from a file based catalog through a
# Subscription (what the console's Install button creates), creates a
# ReviewPlatform with an empty-ish spec and waits for it to become Ready.
#
#   operator/hack/kind-olm-test.sh            # set up and test
#   operator/hack/kind-olm-test.sh --delete   # remove the cluster and registry
#
# Needs: podman (rootful machine on macOS), kind, kubectl, Go. Uses kind context
# kind-eagle, registry localhost:5001. Nothing touches your other clusters.
set -euo pipefail

CLUSTER=eagle
REG_NAME=kind-registry
REG_PORT=5001
REG=localhost:${REG_PORT}
VERSION=${VERSION:-0.1.0}
OLM_VERSION=${OLM_VERSION:-v0.46.0}
PLATFORM=linux/$(uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/')
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
OPERATOR=$ROOT/operator
export KIND_EXPERIMENTAL_PROVIDER=podman
KCTL="kubectl --context kind-${CLUSTER}"
MAKE_VARS=(IMAGE_TAG_BASE=$REG VERSION=$VERSION)

if [[ ${1:-} == "--delete" ]]; then
  kind delete cluster --name "$CLUSTER" || true
  podman rm -f "$REG_NAME" || true
  exit 0
fi

step() { printf '\n==> %s\n' "$*"; }
push() { podman push -q --tls-verify=false "$1"; }

step "Local registry on $REG"
if ! podman container exists "$REG_NAME"; then
  podman run -d --restart=always -p "127.0.0.1:${REG_PORT}:5000" --name "$REG_NAME" docker.io/library/registry:2 >/dev/null
fi

step "kind cluster '$CLUSTER'"
if ! kind get clusters 2>/dev/null | grep -qx "$CLUSTER"; then
  kind create cluster --name "$CLUSTER" --config - <<'EOF'
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
containerdConfigPatches:
  - |-
    [plugins."io.containerd.grpc.v1.cri".registry]
      config_path = "/etc/containerd/certs.d"
EOF
fi
podman network connect kind "$REG_NAME" 2>/dev/null || true
podman exec "${CLUSTER}-control-plane" sh -c "mkdir -p /etc/containerd/certs.d/${REG} && \
  printf '[host.\"http://${REG_NAME}:5000\"]\n' > /etc/containerd/certs.d/${REG}/hosts.toml"

step "Build and push images ($PLATFORM)"
podman build -q --platform "$PLATFORM" -t "$REG/eagle:v$VERSION" -f "$ROOT/Containerfile" "$ROOT" >/dev/null
podman build -q --platform "$PLATFORM" -t "$REG/eagle-agent-runner:v$VERSION" -f "$ROOT/agent-runner/Containerfile" "$ROOT" >/dev/null
make -C "$OPERATOR" docker-build IMG="$REG/eagle-operator:v$VERSION" PLATFORM="$PLATFORM" >/dev/null
for image in eagle eagle-agent-runner eagle-operator; do push "$REG/$image:v$VERSION"; done

step "Bundle and catalog"
make -C "$OPERATOR" bundle "${MAKE_VARS[@]}" | tail -1
make -C "$OPERATOR" bundle-build "${MAKE_VARS[@]}" >/dev/null
push "$REG/eagle-operator-bundle:v$VERSION"
make -C "$OPERATOR" catalog "${MAKE_VARS[@]}" OPM_IN_CONTAINER=1 OPM_RENDER_FLAGS=--use-http >/dev/null
make -C "$OPERATOR" catalog-build "${MAKE_VARS[@]}" >/dev/null
push "$REG/eagle-operator-catalog:v$VERSION"

step "OLM $OLM_VERSION"
if ! $KCTL get crd clusterserviceversions.operators.coreos.com >/dev/null 2>&1; then
  "$OPERATOR/bin/operator-sdk" olm install --version "$OLM_VERSION" --timeout 5m >/dev/null
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

step "Smoke test through a port-forward"
$KCTL port-forward -n eagle svc/eagle-web 18000:8000 >/dev/null 2>&1 &
PF=$!
trap 'kill $PF 2>/dev/null || true' EXIT
for _ in $(seq 1 15); do curl -sf localhost:18000/healthz >/dev/null && break; sleep 1; done
curl -sf localhost:18000/readyz
echo
curl -sf -o /dev/null -w 'UI: HTTP %{http_code}\n' localhost:18000/
curl -sf -H 'Content-Type: application/json' -X POST localhost:18000/api/v1/auth/login \
  -d '{"username":"admin","password":"kind-test-password"}' -o /dev/null -w 'admin login: HTTP %{http_code}\n'
restarts=$($KCTL get pods -n eagle -l app.kubernetes.io/component=eagle-worker -o jsonpath='{.items[0].status.containerStatuses[0].restartCount}')
echo "worker restarts: $restarts"
[[ $restarts == 0 ]]


step "Restore the release bundle (placeholder images, not $REG)"
make -C "$OPERATOR" bundle >/dev/null
rm -rf "$OPERATOR/catalog" "$OPERATOR/catalog.Dockerfile"

printf '\nOK. Use it with:\n  %s port-forward -n eagle svc/eagle-web 18000:8000\n  open http://localhost:18000 (admin / kind-test-password)\nRemove with: %s --delete\n' "$KCTL" "$0"
