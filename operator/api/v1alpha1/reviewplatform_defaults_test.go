package v1alpha1

import (
	"strings"
	"testing"

	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/resource"
	"k8s.io/utils/ptr"
)

func validSpec() ReviewPlatformSpec {
	return ReviewPlatformSpec{Image: "app:1", Runner: RunnerSpec{Image: "runner:1"}}
}

func TestDatabaseValidate(t *testing.T) {
	ext := &ExternalDatabaseSpec{SecretName: "db", Key: "url"}
	cases := []struct {
		name    string
		db      DatabaseSpec
		wantErr string
		managed bool
	}{
		{name: "empty defaults to managed", db: DatabaseSpec{}, managed: true},
		{name: "managed object without enabled", db: DatabaseSpec{Managed: &ManagedDatabaseSpec{}}, managed: true},
		{name: "managed enabled", db: DatabaseSpec{Managed: &ManagedDatabaseSpec{Enabled: ptr.To(true)}}, managed: true},
		{name: "external only", db: DatabaseSpec{External: ext}, managed: false},
		{name: "external with managed disabled", db: DatabaseSpec{External: ext, Managed: &ManagedDatabaseSpec{Enabled: ptr.To(false)}}, managed: false},
		{name: "both enabled", db: DatabaseSpec{External: ext, Managed: &ManagedDatabaseSpec{Enabled: ptr.To(true)}}, wantErr: "mutually exclusive"},
		{name: "both, enabled defaulted", db: DatabaseSpec{External: ext, Managed: &ManagedDatabaseSpec{}}, wantErr: "mutually exclusive"},
		{name: "neither", db: DatabaseSpec{Managed: &ManagedDatabaseSpec{Enabled: ptr.To(false)}}, wantErr: "external is required"},
		{name: "external without secret name", db: DatabaseSpec{External: &ExternalDatabaseSpec{}}, wantErr: "secretName is required"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			err := tc.db.Validate()
			if tc.wantErr == "" {
				if err != nil {
					t.Fatalf("unexpected error: %v", err)
				}
				s := validSpec()
				s.Database = tc.db
				if got := s.ManagedDatabaseEnabled(); got != tc.managed {
					t.Fatalf("ManagedDatabaseEnabled = %v, want %v", got, tc.managed)
				}
				return
			}
			if err == nil || !strings.Contains(err.Error(), tc.wantErr) {
				t.Fatalf("error = %v, want containing %q", err, tc.wantErr)
			}
		})
	}
}

func TestSpecValidate(t *testing.T) {
	s := validSpec()
	if err := s.Validate(); err != nil {
		t.Fatalf("valid spec rejected: %v", err)
	}

	bad := ReviewPlatformSpec{Runner: RunnerSpec{BlockedCIDRs: []string{"nope", "fd00::/8", "10.0.0.0/8"}}}
	err := bad.Validate()
	if err == nil {
		t.Fatal("expected error")
	}
	for _, want := range []string{"spec.image", "spec.runner.image", `"nope" is not a valid CIDR`, `"fd00::/8" is not an IPv4 CIDR`} {
		if !strings.Contains(err.Error(), want) {
			t.Errorf("error %q does not mention %q", err, want)
		}
	}
	if strings.Contains(err.Error(), "10.0.0.0/8") {
		t.Errorf("valid CIDR reported as invalid: %v", err)
	}

	s.BootstrapAdmin.PasswordSecretRef = &SecretKeyRef{}
	if err := s.Validate(); err == nil || !strings.Contains(err.Error(), "passwordSecretRef.name") {
		t.Fatalf("expected passwordSecretRef error, got %v", err)
	}
}

func TestDefaults(t *testing.T) {
	s := validSpec()
	if s.WebReplicas() != 1 || s.WorkerReplicaCount() != 1 {
		t.Error("replica defaults should be 1")
	}
	if !s.RouteEnabled() {
		t.Error("route should default to enabled")
	}
	if s.BootstrapAdminUsername() != "admin" {
		t.Error("bootstrap admin should default to admin")
	}
	if s.PostgresImage() != DefaultPostgresImage {
		t.Error("postgres image default")
	}
	if q := s.PostgresStorageSize(); q.Cmp(resource.MustParse("5Gi")) != 0 {
		t.Errorf("storage default = %s", q.String())
	}
	if s.ExternalDatabaseKey() != "url" {
		t.Error("external key default")
	}
	cpu, mem := s.RunnerLimits()
	if cpu != "2" || mem != "4Gi" {
		t.Errorf("runner limits = %s/%s", cpu, mem)
	}
	got := s.RunnerBlockedCIDRs()
	if strings.Join(got, ",") != "10.0.0.0/8,172.16.0.0/12,192.168.0.0/16" {
		t.Errorf("blocked CIDRs = %v", got)
	}
	// Mutating the returned slice must not change the package default.
	got[0] = "x"
	if DefaultBlockedCIDRs[0] != "10.0.0.0/8" {
		t.Error("RunnerBlockedCIDRs leaked the default slice")
	}

	s.Runner.Resources.Limits = corev1.ResourceList{
		corev1.ResourceCPU:    resource.MustParse("500m"),
		corev1.ResourceMemory: resource.MustParse("1Gi"),
	}
	s.Runner.BlockedCIDRs = []string{"100.64.0.0/10"}
	s.Route.Enabled = ptr.To(false)
	s.Replicas = ptr.To[int32](3)
	cpu, mem = s.RunnerLimits()
	if cpu != "500m" || mem != "1Gi" {
		t.Errorf("runner limits = %s/%s", cpu, mem)
	}
	if len(s.RunnerBlockedCIDRs()) != 1 || s.RouteEnabled() || s.WebReplicas() != 3 {
		t.Error("explicit values not honored")
	}
}

func TestSSOValidateAndEnabled(t *testing.T) {
	s := validSpec()
	if s.SSOEnabled() {
		t.Error("SSO disabled without spec.sso")
	}
	s.SSO = &SSOSpec{Issuer: "https://idp"}
	if s.SSOEnabled() {
		t.Error("SSO requires clientId too")
	}
	s.SSO.ClientID = "eagle"
	if !s.SSOEnabled() {
		t.Error("SSO should be enabled with issuer and clientId")
	}
	if err := s.Validate(); err != nil {
		t.Fatalf("valid SSO rejected: %v", err)
	}
	s.SSO.DefaultRole = "owner"
	s.SSO.ClientSecretRef = &SecretKeyRef{}
	err := s.Validate()
	if err == nil || !strings.Contains(err.Error(), "defaultRole") || !strings.Contains(err.Error(), "clientSecretRef.name") {
		t.Fatalf("expected defaultRole and clientSecretRef errors, got %v", err)
	}
}
