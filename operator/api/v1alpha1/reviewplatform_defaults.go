package v1alpha1

import (
	"errors"
	"fmt"
	"net"

	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/resource"
)

// The helpers below resolve defaults in code as well as in the CRD schema, so
// the controller behaves the same when an object was created before a default
// existed or when the schema defaults were bypassed (for example in tests).

// WebReplicas returns spec.replicas or 1.
func (s *ReviewPlatformSpec) WebReplicas() int32 {
	if s.Replicas == nil {
		return 1
	}
	return *s.Replicas
}

// WorkerReplicaCount returns spec.workerReplicas or 1.
func (s *ReviewPlatformSpec) WorkerReplicaCount() int32 {
	if s.WorkerReplicas == nil {
		return 1
	}
	return *s.WorkerReplicas
}

// RouteEnabled returns spec.route.enabled or true.
func (s *ReviewPlatformSpec) RouteEnabled() bool {
	return s.Route.Enabled == nil || *s.Route.Enabled
}

// ManagedDatabaseEnabled reports whether the operator should run Postgres.
// With no database configuration at all, the managed database is used.
func (s *ReviewPlatformSpec) ManagedDatabaseEnabled() bool {
	m := s.Database.Managed
	if m == nil {
		return s.Database.External == nil
	}
	return m.Enabled == nil || *m.Enabled
}

// PostgresImage returns the managed database image or the default.
func (s *ReviewPlatformSpec) PostgresImage() string {
	if m := s.Database.Managed; m != nil && m.Image != "" {
		return m.Image
	}
	return DefaultPostgresImage
}

// PostgresStorageSize returns the managed database volume size or the default.
func (s *ReviewPlatformSpec) PostgresStorageSize() resource.Quantity {
	if m := s.Database.Managed; m != nil && m.StorageSize != nil && !m.StorageSize.IsZero() {
		return *m.StorageSize
	}
	return DefaultStorageSize.DeepCopy()
}

// ExternalDatabaseKey returns the key holding the URL in the external secret.
func (s *ReviewPlatformSpec) ExternalDatabaseKey() string {
	if e := s.Database.External; e != nil && e.Key != "" {
		return e.Key
	}
	return DefaultExternalDatabaseKey
}

// BootstrapAdminUsername returns the configured username or "admin".
func (s *ReviewPlatformSpec) BootstrapAdminUsername() string {
	if s.BootstrapAdmin.Username != "" {
		return s.BootstrapAdmin.Username
	}
	return DefaultBootstrapAdminUsername
}

// RunnerBlockedCIDRs returns the configured list or the defaults when empty.
func (s *ReviewPlatformSpec) RunnerBlockedCIDRs() []string {
	if len(s.Runner.BlockedCIDRs) == 0 {
		return append([]string(nil), DefaultBlockedCIDRs...)
	}
	return append([]string(nil), s.Runner.BlockedCIDRs...)
}

// RunnerLimits returns the runner cpu and memory limits as strings.
func (s *ReviewPlatformSpec) RunnerLimits() (cpu, memory string) {
	cpu, memory = DefaultRunnerCPULimit, DefaultRunnerMemoryLimit
	if q, ok := s.Runner.Resources.Limits[corev1.ResourceCPU]; ok && !q.IsZero() {
		cpu = q.String()
	}
	if q, ok := s.Runner.Resources.Limits[corev1.ResourceMemory]; ok && !q.IsZero() {
		memory = q.String()
	}
	return cpu, memory
}

// Validate checks the rules the CRD schema cannot fully express (or that must
// hold even if the schema was bypassed). It returns all problems joined.
func (s *ReviewPlatformSpec) Validate() error {
	var errs []error
	if s.Image == "" {
		errs = append(errs, errors.New("spec.image is required (the operator has no default application image; set RELATED_IMAGE_EAGLE on the operator or spec.image)"))
	}
	if s.Runner.Image == "" {
		errs = append(errs, errors.New("spec.runner.image is required (the operator has no default runner image; set RELATED_IMAGE_EAGLE_RUNNER on the operator or spec.runner.image)"))
	}
	if err := s.Database.Validate(); err != nil {
		errs = append(errs, err)
	}
	for _, c := range s.Runner.BlockedCIDRs {
		ip, _, err := net.ParseCIDR(c)
		if err != nil {
			errs = append(errs, fmt.Errorf("spec.runner.blockedCIDRs: %q is not a valid CIDR", c))
			continue
		}
		if ip.To4() == nil {
			errs = append(errs, fmt.Errorf("spec.runner.blockedCIDRs: %q is not an IPv4 CIDR", c))
		}
	}
	if r := s.BootstrapAdmin.PasswordSecretRef; r != nil && r.Name == "" {
		errs = append(errs, errors.New("spec.bootstrapAdmin.passwordSecretRef.name is required"))
	}
	if sso := s.SSO; sso != nil {
		if r := sso.ClientSecretRef; r != nil && r.Name == "" {
			errs = append(errs, errors.New("spec.sso.clientSecretRef.name is required"))
		}
		switch sso.DefaultRole {
		case "", "admin", "member", "viewer", "none":
		default:
			errs = append(errs, fmt.Errorf("spec.sso.defaultRole: %q is not one of admin, member, viewer, none", sso.DefaultRole))
		}
	}
	return errors.Join(errs...)
}

// SSOEnabled reports whether OIDC is configured (issuer and clientId set).
func (s *ReviewPlatformSpec) SSOEnabled() bool {
	return s.SSO != nil && s.SSO.Issuer != "" && s.SSO.ClientID != ""
}

// Validate enforces that exactly one of managed or external is in effect.
func (d *DatabaseSpec) Validate() error {
	managedExplicitlyOn := d.Managed != nil && (d.Managed.Enabled == nil || *d.Managed.Enabled)
	managedExplicitlyOff := d.Managed != nil && d.Managed.Enabled != nil && !*d.Managed.Enabled
	switch {
	case d.External != nil && managedExplicitlyOn:
		return errors.New("spec.database: managed and external are mutually exclusive (set managed.enabled=false or remove external)")
	case d.External == nil && managedExplicitlyOff:
		return errors.New("spec.database: external is required when managed.enabled is false")
	case d.External != nil && d.External.SecretName == "":
		return errors.New("spec.database.external.secretName is required")
	}
	return nil
}
