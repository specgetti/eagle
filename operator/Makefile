# Release version (bundle, CSV and default image tags).
VERSION ?= 0.1.0
# Registry and organization for every image below.
IMAGE_TAG_BASE ?= quay.io/hmoghani

# Operator image.
IMG ?= $(IMAGE_TAG_BASE)/eagle-operator:v$(VERSION)
# Default images the operator uses when a ReviewPlatform leaves them empty.
APP_IMG ?= $(IMAGE_TAG_BASE)/eagle:v$(VERSION)
RUNNER_IMG ?= $(IMAGE_TAG_BASE)/eagle-agent-runner:v$(VERSION)
POSTGRES_IMG ?= registry.redhat.io/rhel9/postgresql-16:latest
# OLM bundle and file based catalog images (OperatorHub install).
BUNDLE_IMG ?= $(IMAGE_TAG_BASE)/eagle-operator-bundle:v$(VERSION)
CATALOG_IMG ?= $(IMAGE_TAG_BASE)/eagle-operator-catalog:v$(VERSION)
CHANNELS ?= alpha
DEFAULT_CHANNEL ?= alpha
# Set to the version this release upgrades from (e.g. 0.1.0) to keep it in the
# catalog and add an upgrade edge; empty for the first release.
PREVIOUS_VERSION ?=
PREVIOUS_BUNDLE_IMG ?= $(IMAGE_TAG_BASE)/eagle-operator-bundle:v$(PREVIOUS_VERSION)
# Extra flags for `opm render`, e.g. --use-http for a local plain HTTP registry.
OPM_RENDER_FLAGS ?=
# On macOS opm cannot pull images without a containers policy.json. Set
# OPM_IN_CONTAINER=1 to run `opm render` in the opm image with hack/policy.json
# (the containers default policy: no signature checks, as plain `podman pull`).
OPM_IN_CONTAINER ?=
ifeq ($(OPM_IN_CONTAINER),1)
OPM_RENDER = $(CONTAINER_TOOL) run --rm --network host -v $(CURDIR)/hack/policy.json:/etc/containers/policy.json:ro,Z quay.io/operator-framework/opm:$(OPM_VERSION) render
else
OPM_RENDER = $(OPM) render
endif
# podman or docker
CONTAINER_TOOL ?= podman
PLATFORM ?= linux/amd64

# Kubernetes CLI (oc or kubectl).
KUBECTL ?= oc

GOBIN ?= $(shell go env GOPATH)/bin
SHELL = /usr/bin/env bash -o pipefail
.SHELLFLAGS = -ec

.PHONY: all
all: build

##@ General

.PHONY: help
help: ## Display this help.
	@awk 'BEGIN {FS = ":.*##"; printf "\nUsage:\n  make \033[36m<target>\033[0m\n"} /^[a-zA-Z_0-9-]+:.*?##/ { printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2 } /^##@/ { printf "\n\033[1m%s\033[0m\n", substr($$0, 5) } ' $(MAKEFILE_LIST)

##@ Development

.PHONY: manifests
manifests: controller-gen ## Generate CRD and ClusterRole manifests.
	$(CONTROLLER_GEN) rbac:roleName=manager-role crd paths="./..." output:crd:artifacts:config=config/crd/bases

.PHONY: generate
generate: controller-gen ## Generate DeepCopy implementations.
	$(CONTROLLER_GEN) object:headerFile="hack/boilerplate.go.txt" paths="./..."

.PHONY: fmt
fmt: ## Run go fmt.
	go fmt ./...

.PHONY: vet
vet: ## Run go vet.
	go vet ./...

.PHONY: test
test: manifests generate fmt vet ## Run unit and fake client reconcile tests.
	go test ./... -coverprofile cover.out

.PHONY: test-envtest
test-envtest: manifests generate setup-envtest ## Also run the envtest suite (real kube-apiserver and etcd).
	KUBEBUILDER_ASSETS="$$($(SETUP_ENVTEST) use $(ENVTEST_K8S_VERSION) --bin-dir $(LOCALBIN) -p path)" go test ./... -coverprofile cover.out

.PHONY: verify
verify: manifests generate ## Fail if generated files are out of date (for CI).
	git diff --exit-code -- api config

##@ Build

.PHONY: build
build: manifests generate fmt vet ## Build the manager binary.
	go build -o bin/manager cmd/main.go

.PHONY: run
run: manifests generate fmt vet ## Run the controller against the current kubeconfig.
	go run ./cmd/main.go

.PHONY: docker-build
docker-build: ## Build the operator image.
	$(CONTAINER_TOOL) build --platform $(PLATFORM) -t $(IMG) .

.PHONY: docker-push
docker-push: ## Push the operator image.
	$(CONTAINER_TOOL) push $(IMG)

##@ Deployment

.PHONY: install
install: manifests kustomize ## Install the CRD into the cluster.
	$(KUSTOMIZE) build config/crd | $(KUBECTL) apply -f -

.PHONY: uninstall
uninstall: manifests kustomize ## Remove the CRD (deletes every ReviewPlatform).
	$(KUSTOMIZE) build config/crd | $(KUBECTL) delete --ignore-not-found -f -

.PHONY: deploy
deploy: manifests kustomize set-images ## Deploy the operator (IMG) with its default images.
	cd config/manager && $(KUSTOMIZE) edit set image controller=$(IMG)
	$(KUSTOMIZE) build config/default | $(KUBECTL) apply -f -

.PHONY: undeploy
undeploy: kustomize ## Remove the operator deployment.
	$(KUSTOMIZE) build config/default | $(KUBECTL) delete --ignore-not-found -f -

##@ OLM bundle and catalog (OperatorHub install)

.PHONY: set-images
set-images: kustomize ## Write the default images (APP_IMG, RUNNER_IMG, POSTGRES_IMG) and IMG into config/manager.
	APP_IMG=$(APP_IMG) RUNNER_IMG=$(RUNNER_IMG) POSTGRES_IMG=$(POSTGRES_IMG) \
		envsubst < config/manager/default_images.yaml.in > config/manager/default_images.yaml
	cd config/manager && $(KUSTOMIZE) edit set image controller=$(IMG)

.PHONY: bundle
bundle: manifests kustomize operator-sdk set-images ## Generate and validate the OLM bundle in bundle/.
	$(OPERATOR_SDK) generate kustomize manifests -q --apis-dir api
	$(KUSTOMIZE) build config/manifests | $(OPERATOR_SDK) generate bundle -q --overwrite \
		--version $(VERSION) --package eagle-operator \
		--channels $(CHANNELS) --default-channel $(DEFAULT_CHANNEL)
	sed -i.bak -e 's|containerImage: .*|containerImage: $(IMG)|' bundle/manifests/eagle-operator.clusterserviceversion.yaml
	rm -f bundle/manifests/*.bak
	$(OPERATOR_SDK) bundle validate ./bundle --select-optional suite=operatorframework

.PHONY: bundle-build
bundle-build: ## Build the bundle image.
	$(CONTAINER_TOOL) build -f bundle.Dockerfile -t $(BUNDLE_IMG) .

.PHONY: bundle-push
bundle-push: ## Push the bundle image.
	$(CONTAINER_TOOL) push $(BUNDLE_IMG)

.PHONY: catalog
catalog: opm ## Render a file based catalog for BUNDLE_IMG (the bundle image must be pushed first).
	rm -rf catalog && mkdir -p catalog/eagle-operator
	$(OPM) init eagle-operator --default-channel=$(DEFAULT_CHANNEL) \
		--icon=config/manifests/icon.svg --output=yaml > catalog/eagle-operator/index.yaml
	for img in $(if $(PREVIOUS_VERSION),$(PREVIOUS_BUNDLE_IMG)) $(BUNDLE_IMG); do \
		$(OPM_RENDER) $$img --output=yaml $(OPM_RENDER_FLAGS) >> catalog/eagle-operator/index.yaml; \
	done
	printf -- '---\nschema: olm.channel\npackage: eagle-operator\nname: %s\nentries:\n' $(DEFAULT_CHANNEL) >> catalog/eagle-operator/index.yaml
ifneq ($(PREVIOUS_VERSION),)
	printf -- '  - name: eagle-operator.v%s\n' $(PREVIOUS_VERSION) >> catalog/eagle-operator/index.yaml
	printf -- '  - name: eagle-operator.v%s\n    replaces: eagle-operator.v%s\n' $(VERSION) $(PREVIOUS_VERSION) >> catalog/eagle-operator/index.yaml
else
	printf -- '  - name: eagle-operator.v%s\n' $(VERSION) >> catalog/eagle-operator/index.yaml
endif
	$(OPM) validate catalog
	rm -f catalog.Dockerfile
	$(OPM) generate dockerfile catalog -i quay.io/operator-framework/opm:$(OPM_VERSION) -b quay.io/operator-framework/opm:$(OPM_VERSION)

.PHONY: catalog-build
catalog-build: ## Build the catalog image.
	$(CONTAINER_TOOL) build -f catalog.Dockerfile -t $(CATALOG_IMG) .

.PHONY: catalog-push
catalog-push: ## Push the catalog image.
	$(CONTAINER_TOOL) push $(CATALOG_IMG)

##@ Tools

LOCALBIN ?= $(shell pwd)/bin
$(LOCALBIN):
	mkdir -p $(LOCALBIN)

CONTROLLER_GEN ?= $(LOCALBIN)/controller-gen
KUSTOMIZE ?= $(LOCALBIN)/kustomize

SETUP_ENVTEST ?= $(LOCALBIN)/setup-envtest

CONTROLLER_TOOLS_VERSION ?= v0.18.0
SETUP_ENVTEST_VERSION ?= release-0.21
ENVTEST_K8S_VERSION ?= 1.33.x
KUSTOMIZE_VERSION ?= v5.6.0
OPERATOR_SDK_VERSION ?= v1.42.3
OPM_VERSION ?= v1.74.0

OPERATOR_SDK ?= $(LOCALBIN)/operator-sdk
OPM ?= $(LOCALBIN)/opm
OS := $(shell go env GOOS)
ARCH := $(shell go env GOARCH)

.PHONY: controller-gen
controller-gen: $(CONTROLLER_GEN) ## Download controller-gen locally if needed.
$(CONTROLLER_GEN): | $(LOCALBIN)
	GOBIN=$(LOCALBIN) go install sigs.k8s.io/controller-tools/cmd/controller-gen@$(CONTROLLER_TOOLS_VERSION)

.PHONY: kustomize
kustomize: $(KUSTOMIZE) ## Download kustomize locally if needed.
$(KUSTOMIZE): | $(LOCALBIN)
	GOBIN=$(LOCALBIN) go install sigs.k8s.io/kustomize/kustomize/v5@$(KUSTOMIZE_VERSION)

.PHONY: setup-envtest
setup-envtest: $(SETUP_ENVTEST) ## Download setup-envtest locally if needed.
$(SETUP_ENVTEST): | $(LOCALBIN)
	GOBIN=$(LOCALBIN) go install sigs.k8s.io/controller-runtime/tools/setup-envtest@$(SETUP_ENVTEST_VERSION)

.PHONY: operator-sdk
operator-sdk: $(OPERATOR_SDK) ## Download operator-sdk locally if needed.
$(OPERATOR_SDK): | $(LOCALBIN)
	curl -sSLfo $(OPERATOR_SDK) https://github.com/operator-framework/operator-sdk/releases/download/$(OPERATOR_SDK_VERSION)/operator-sdk_$(OS)_$(ARCH)
	chmod +x $(OPERATOR_SDK)

.PHONY: opm
opm: $(OPM) ## Download opm locally if needed.
$(OPM): | $(LOCALBIN)
	curl -sSLfo $(OPM) https://github.com/operator-framework/operator-registry/releases/download/$(OPM_VERSION)/$(OS)-$(ARCH)-opm
	chmod +x $(OPM)
