package controller

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"

	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	networkingv1 "k8s.io/api/networking/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"
	"k8s.io/utils/ptr"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/envtest"

	eaglev1alpha1 "github.com/specgetti/eagle/operator/api/v1alpha1"
)

// TestEnvtest runs against a real kube-apiserver and etcd. It checks the
// generated CRD (defaults and CEL validation) and that every object the
// reconciler builds is accepted by the API server. It is skipped unless
// KUBEBUILDER_ASSETS points at envtest binaries (see `make test-envtest`).
func TestEnvtest(t *testing.T) {
	if os.Getenv("KUBEBUILDER_ASSETS") == "" {
		t.Skip("KUBEBUILDER_ASSETS not set; run `make test-envtest`")
	}
	env := &envtest.Environment{
		CRDDirectoryPaths:     []string{filepath.Join("..", "..", "config", "crd", "bases")},
		ErrorIfCRDPathMissing: true,
	}
	cfg, err := env.Start()
	if err != nil {
		t.Fatalf("starting envtest: %v", err)
	}
	t.Cleanup(func() { _ = env.Stop() })

	s := testScheme(t)
	c, err := client.New(cfg, client.Options{Scheme: s})
	if err != nil {
		t.Fatal(err)
	}
	ctx := context.Background()
	ns := &corev1.Namespace{ObjectMeta: metav1.ObjectMeta{Name: "team"}}
	if err := c.Create(ctx, ns); err != nil {
		t.Fatal(err)
	}

	newCR := func(name string) *eaglev1alpha1.ReviewPlatform {
		return &eaglev1alpha1.ReviewPlatform{
			ObjectMeta: metav1.ObjectMeta{Name: name, Namespace: "team"},
			Spec: eaglev1alpha1.ReviewPlatformSpec{
				Image:  "quay.io/x/eagle:1",
				Runner: eaglev1alpha1.RunnerSpec{Image: "quay.io/x/runner:1"},
			},
		}
	}

	t.Run("empty spec is accepted (console form default)", func(t *testing.T) {
		rp := &eaglev1alpha1.ReviewPlatform{ObjectMeta: metav1.ObjectMeta{Name: "empty", Namespace: "team"}}
		if err := c.Create(ctx, rp); err != nil {
			t.Fatalf("a ReviewPlatform with no fields must be valid: %v", err)
		}
	})

	t.Run("schema defaults", func(t *testing.T) {
		rp := newCR("defaults")
		rp.Spec.Database.Managed = &eaglev1alpha1.ManagedDatabaseSpec{}
		rp.Spec.SSO = &eaglev1alpha1.SSOSpec{Issuer: "https://idp", ClientID: "eagle"}
		if err := c.Create(ctx, rp); err != nil {
			t.Fatal(err)
		}
		got := &eaglev1alpha1.ReviewPlatform{}
		if err := c.Get(ctx, client.ObjectKeyFromObject(rp), got); err != nil {
			t.Fatal(err)
		}
		sp := got.Spec
		if sp.Replicas == nil || *sp.Replicas != 1 || sp.WorkerReplicas == nil || *sp.WorkerReplicas != 1 {
			t.Errorf("replica defaults: %v %v", sp.Replicas, sp.WorkerReplicas)
		}
		if strings.Join(sp.Runner.BlockedCIDRs, ",") != "10.0.0.0/8,172.16.0.0/12,192.168.0.0/16" {
			t.Errorf("blockedCIDRs default = %v", sp.Runner.BlockedCIDRs)
		}
		m := sp.Database.Managed
		// The image has no schema default: the operator fills it from
		// RELATED_IMAGE_POSTGRESQL so upgrades and mirrored installs apply.
		if m.Enabled == nil || !*m.Enabled || m.Image != "" || m.StorageSize.String() != "5Gi" {
			t.Errorf("managed defaults = %+v", m)
		}
		sso := sp.SSO
		if sso.Scopes != "openid email profile" || sso.DefaultRole != "viewer" ||
			sso.LocalLoginEnabled == nil || !*sso.LocalLoginEnabled {
			t.Errorf("sso defaults = %+v", sso)
		}
	})

	t.Run("schema rejects bad sso defaultRole", func(t *testing.T) {
		rp := newCR("badrole")
		rp.Spec.SSO = &eaglev1alpha1.SSOSpec{DefaultRole: "owner"}
		if err := c.Create(ctx, rp); err == nil {
			t.Fatal("expected enum validation error")
		}
	})

	t.Run("CEL rejects both databases", func(t *testing.T) {
		rp := newCR("both")
		rp.Spec.Database = eaglev1alpha1.DatabaseSpec{
			Managed:  &eaglev1alpha1.ManagedDatabaseSpec{},
			External: &eaglev1alpha1.ExternalDatabaseSpec{SecretName: "x"},
		}
		err := c.Create(ctx, rp)
		if err == nil || !strings.Contains(err.Error(), "mutually exclusive") {
			t.Fatalf("want mutually exclusive error, got %v", err)
		}
	})

	t.Run("CEL rejects no database", func(t *testing.T) {
		rp := newCR("none")
		rp.Spec.Database.Managed = &eaglev1alpha1.ManagedDatabaseSpec{Enabled: ptr.To(false)}
		err := c.Create(ctx, rp)
		if err == nil || !strings.Contains(err.Error(), "external is required") {
			t.Fatalf("want external required error, got %v", err)
		}
	})

	t.Run("CEL accepts external with managed disabled", func(t *testing.T) {
		rp := newCR("external")
		rp.Spec.Database = eaglev1alpha1.DatabaseSpec{
			Managed:  &eaglev1alpha1.ManagedDatabaseSpec{Enabled: ptr.To(false)},
			External: &eaglev1alpha1.ExternalDatabaseSpec{SecretName: "x"},
		}
		if err := c.Create(ctx, rp); err != nil {
			t.Fatal(err)
		}
	})

	t.Run("reconcile against a real API server", func(t *testing.T) {
		rp := newCR("eagle")
		if err := c.Create(ctx, rp); err != nil {
			t.Fatal(err)
		}
		// envtest has no route.openshift.io, which also exercises that path.
		r := &ReviewPlatformReconciler{Client: c, Scheme: s, APIReader: c, RoutesAvailable: false}
		req := ctrl.Request{NamespacedName: types.NamespacedName{Namespace: "team", Name: "eagle"}}
		for i := 0; i < 2; i++ {
			if _, err := r.Reconcile(ctx, req); err != nil {
				t.Fatalf("reconcile %d: %v", i, err)
			}
		}
		for name, obj := range map[string]client.Object{
			"eagle-web":    &appsv1.Deployment{},
			"eagle-worker": &appsv1.Deployment{},
			"eagle-db":     &appsv1.StatefulSet{},
			"eagle-runner": &networkingv1.NetworkPolicy{},
			"eagle-keys":   &corev1.Secret{},
		} {
			if err := c.Get(ctx, types.NamespacedName{Namespace: "team", Name: name}, obj); err != nil {
				t.Errorf("get %T %s: %v", obj, name, err)
			}
		}
		got := &eaglev1alpha1.ReviewPlatform{}
		if err := c.Get(ctx, req.NamespacedName, got); err != nil {
			t.Fatal(err)
		}
		if got.Status.Phase != eaglev1alpha1.PhaseProgressing || len(got.Status.Conditions) != 3 {
			t.Errorf("status = %+v", got.Status)
		}
	})
}
