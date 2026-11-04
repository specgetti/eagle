# Build the manager binary on UBI 9.
FROM registry.access.redhat.com/ubi9/go-toolset:1.24 AS builder
ARG TARGETOS
ARG TARGETARCH

WORKDIR /opt/app-root/src
COPY --chown=1001:0 go.mod go.sum ./
RUN go mod download

COPY --chown=1001:0 cmd/ cmd/
COPY --chown=1001:0 api/ api/
COPY --chown=1001:0 internal/ internal/

RUN CGO_ENABLED=0 GOOS=${TARGETOS:-linux} GOARCH=${TARGETARCH} \
    go build -trimpath -ldflags="-s -w" -o manager cmd/main.go

FROM registry.access.redhat.com/ubi9/ubi-minimal:latest
LABEL name="eagle-operator" \
      summary="Operator for the Eagle PR review platform" \
      description="Reconciles ReviewPlatform resources into the Eagle web, worker, database and runner policy objects."
WORKDIR /
COPY --from=builder /opt/app-root/src/manager /manager
USER 65532:65532
ENTRYPOINT ["/manager"]
