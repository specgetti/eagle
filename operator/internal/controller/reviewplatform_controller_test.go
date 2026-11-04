package controller

import (
	"bytes"
	"context"
	"testing"

	routev1 "github.com/openshift/api/route/v1"
	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	networkingv1 "k8s.io/api/networking/v1"
	rbacv1 "k8s.io/api/rbac/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/api/meta"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/types"
	clientgoscheme "k8s.io/client-go/kubernetes/scheme"
	"k8s.io/utils/ptr"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/client/fake"

	eaglev1alpha1 "github.com/hmoghani/eagle/operator/api/v1alpha1"
)

func testScheme(t *testing.T) *runtime.Scheme {
	t.Helper()
	s := runtime.NewScheme()
	for _, add := range []func(*runtime.Scheme) error{clientgoscheme.AddToScheme, routev1.AddToScheme, eaglev1alpha1.AddToScheme} {
		if err := add(s); err != nil {
			t.Fatal(err)
		}
	}
	return s
}

type harness struct {
	t   *testing.T
	c   client.Client
	r   *ReviewPlatformReconciler
	key types.NamespacedName
}

func newHarness(t *testing.T, rp *eaglev1alpha1.ReviewPlatform, objs ...client.Object) *harness {
	t.Helper()
	s := testScheme(t)
	rp.UID = ""
	rp.ResourceVersion = ""
	objs = append(objs, rp)
	c := fake.NewClientBuilder().WithScheme(s).WithObjects(objs...).
		WithStatusSubresource(&eaglev1alpha1.ReviewPlatform{}).Build()
	return &harness{
		t:   t,
		c:   c,
		r:   &ReviewPlatformReconciler{Client: c, Scheme: s, APIReader: c, RoutesAvailable: true},
		key: types.NamespacedName{Namespace: rp.Namespace, Name: rp.Name},
	}
}

func (h *harness) reconcile() ctrl.Result {
	h.t.Helper()
	res, err := h.r.Reconcile(context.Background(), ctrl.Request{NamespacedName: h.key})
	if err != nil {
		h.t.Fatalf("reconcile: %v", err)
	}
	return res
}

func (h *harness) get(name string, obj client.Object) error {
	return h.c.Get(context.Background(), types.NamespacedName{Namespace: h.key.Namespace, Name: name}, obj)
}

func (h *harness) mustGet(name string, obj client.Object) {
	h.t.Helper()
	if err := h.get(name, obj); err != nil {
		h.t.Fatalf("get %T %s: %v", obj, name, err)
	}
}

func (h *harness) cr() *eaglev1alpha1.ReviewPlatform {
	h.t.Helper()
	rp := &eaglev1alpha1.ReviewPlatform{}
	h.mustGet(h.key.Name, rp)
	return rp
}

func (h *harness) markReady() {
	h.t.Helper()
	ctx := context.Background()
	sts := &appsv1.StatefulSet{}
	if err := h.get("eagle-db", sts); err == nil {
		sts.Status.ReadyReplicas = 1
		sts.Status.Replicas = 1
		if err := h.c.Status().Update(ctx, sts); err != nil {
			h.t.Fatal(err)
		}
	}
	for _, n := range []string{"eagle-web", "eagle-worker"} {
		d := &appsv1.Deployment{}
		h.mustGet(n, d)
		d.Status.AvailableReplicas = 1
		if err := h.c.Status().Update(ctx, d); err != nil {
			h.t.Fatal(err)
		}
	}
}

func isOwnedBy(obj client.Object, rp *eaglev1alpha1.ReviewPlatform) bool {
	return metav1.IsControlledBy(obj, rp)
}

func TestReconcileManagedDefaults(t *testing.T) {
	rp := newRP()
	rp.Spec.Route.Host = "eagle.apps.example.com"
	h := newHarness(t, rp)

	if res := h.reconcile(); res.RequeueAfter == 0 {
		t.Error("expected a requeue while the database is not ready")
	}
	cr := h.cr()

	owned := map[string]client.Object{
		"eagle-web":          &appsv1.Deployment{},
		"eagle-worker":       &appsv1.Deployment{},
		"eagle-db":           &appsv1.StatefulSet{},
		"eagle-database-url": &corev1.Secret{},
	}
	for name, obj := range owned {
		h.mustGet(name, obj)
		if !isOwnedBy(obj, cr) {
			t.Errorf("%T %s must be owned by the CR", obj, name)
		}
	}
	for _, n := range []string{"eagle-web", "eagle-db"} {
		svc := &corev1.Service{}
		h.mustGet(n, svc)
		if !isOwnedBy(svc, cr) {
			t.Errorf("service %s not owned", n)
		}
	}
	for _, n := range []string{"eagle-web", "eagle-worker", "eagle-runner"} {
		sa := &corev1.ServiceAccount{}
		h.mustGet(n, sa)
		if !isOwnedBy(sa, cr) {
			t.Errorf("SA %s not owned", n)
		}
	}
	for _, n := range []string{"eagle-web", "eagle-runner", "eagle-db"} {
		h.mustGet(n, &networkingv1.NetworkPolicy{})
	}
	h.mustGet("eagle-worker", &rbacv1.Role{})
	h.mustGet("eagle-worker", &rbacv1.RoleBinding{})
	route := &routev1.Route{}
	h.mustGet("eagle-web", route)
	if route.Spec.Host != "eagle.apps.example.com" {
		t.Errorf("route host = %s", route.Spec.Host)
	}

	// The runner SA must never be bound to anything.
	rbs := &rbacv1.RoleBindingList{}
	if err := h.c.List(context.Background(), rbs, client.InNamespace("team")); err != nil {
		t.Fatal(err)
	}
	for _, rb := range rbs.Items {
		for _, s := range rb.Subjects {
			if s.Name == "eagle-runner" {
				t.Errorf("runner SA bound by %s", rb.Name)
			}
		}
	}

	// Generated secrets survive CR deletion: no owner references.
	for _, n := range []string{"eagle-keys", "eagle-db"} {
		s := &corev1.Secret{}
		h.mustGet(n, s)
		if len(s.OwnerReferences) != 0 {
			t.Errorf("secret %s must not have owner references", n)
		}
		if s.Annotations[AnnotationRetain] != "true" {
			t.Errorf("secret %s missing retain annotation", n)
		}
	}

	if cr.Status.Phase != eaglev1alpha1.PhaseProgressing {
		t.Errorf("phase = %s", cr.Status.Phase)
	}
	if !meta.IsStatusConditionFalse(cr.Status.Conditions, eaglev1alpha1.ConditionDatabaseReady) {
		t.Error("DatabaseReady should be False before the StatefulSet is ready")
	}
	if cr.Status.URL != "https://eagle.apps.example.com" || cr.Status.ObservedGeneration != cr.Generation {
		t.Errorf("status = %+v", cr.Status)
	}

	web := &appsv1.Deployment{}
	h.mustGet("eagle-web", web)
	env := envMap(web.Spec.Template.Spec.Containers[0].Env)
	assertValue(t, env, "EAGLE_PUBLIC_URL", "https://eagle.apps.example.com")

	h.markReady()
	if res := h.reconcile(); res.RequeueAfter != 0 {
		t.Errorf("unexpected requeue when ready: %+v", res)
	}
	cr = h.cr()
	for _, c := range []string{eaglev1alpha1.ConditionReady, eaglev1alpha1.ConditionDatabaseReady, eaglev1alpha1.ConditionAvailable} {
		if !meta.IsStatusConditionTrue(cr.Status.Conditions, c) {
			t.Errorf("condition %s not True: %+v", c, cr.Status.Conditions)
		}
	}
	if cr.Status.Phase != eaglev1alpha1.PhaseReady {
		t.Errorf("phase = %s", cr.Status.Phase)
	}
}

func TestReconcileNeverRegeneratesSecrets(t *testing.T) {
	rp := newRP()
	h := newHarness(t, rp)
	h.reconcile()

	keys, db := &corev1.Secret{}, &corev1.Secret{}
	h.mustGet("eagle-keys", keys)
	h.mustGet("eagle-db", db)

	for i := 0; i < 3; i++ {
		h.reconcile()
	}
	keys2, db2 := &corev1.Secret{}, &corev1.Secret{}
	h.mustGet("eagle-keys", keys2)
	h.mustGet("eagle-db", db2)
	for k := range keys.Data {
		if !bytes.Equal(keys.Data[k], keys2.Data[k]) {
			t.Errorf("keys secret %s changed", k)
		}
	}
	for k := range db.Data {
		if !bytes.Equal(db.Data[k], db2.Data[k]) {
			t.Errorf("db secret %s changed", k)
		}
	}
	if keys.ResourceVersion != keys2.ResourceVersion || db.ResourceVersion != db2.ResourceVersion {
		t.Error("generated secrets were written again")
	}
}

func TestReconcileKeepsPreexistingGeneratedSecret(t *testing.T) {
	rp := newRP()
	existing := &corev1.Secret{
		ObjectMeta: metav1.ObjectMeta{Name: "eagle-keys", Namespace: "team"},
		Data:       map[string][]byte{KeySecretKey: []byte("old-secret"), KeyEncryptionKey: []byte("old-fernet")},
	}
	h := newHarness(t, rp, existing)
	h.reconcile()
	got := &corev1.Secret{}
	h.mustGet("eagle-keys", got)
	if string(got.Data[KeyEncryptionKey]) != "old-fernet" || string(got.Data[KeySecretKey]) != "old-secret" {
		t.Fatal("pre-existing keys secret was modified")
	}
}

func TestReconcileExistingSecretMissing(t *testing.T) {
	rp := newRP()
	rp.Spec.Secrets.ExistingSecret = "byo-keys"
	h := newHarness(t, rp)
	if res := h.reconcile(); res.RequeueAfter == 0 {
		t.Error("expected requeue while the secret is missing")
	}
	cr := h.cr()
	c := meta.FindStatusCondition(cr.Status.Conditions, eaglev1alpha1.ConditionReady)
	if c == nil || c.Reason != "SecretNotFound" || cr.Status.Phase != eaglev1alpha1.PhaseFailed {
		t.Errorf("status = %+v", cr.Status)
	}
	if err := h.get("eagle-keys", &corev1.Secret{}); !apierrors.IsNotFound(err) {
		t.Error("no keys secret may be generated when existingSecret is set")
	}
	if err := h.get("eagle-web", &appsv1.Deployment{}); !apierrors.IsNotFound(err) {
		t.Error("deployments must not be created without keys")
	}
}

func TestReconcileExternalDatabase(t *testing.T) {
	rp := newRP()
	rp.Spec.Database.External = &eaglev1alpha1.ExternalDatabaseSpec{SecretName: "ext-db", Key: "url"}
	h := newHarness(t, rp)
	h.reconcile()

	if err := h.get("eagle-db", &appsv1.StatefulSet{}); !apierrors.IsNotFound(err) {
		t.Error("no StatefulSet for an external database")
	}
	if err := h.get("eagle-db", &corev1.Secret{}); !apierrors.IsNotFound(err) {
		t.Error("no db credentials for an external database")
	}
	cr := h.cr()
	c := meta.FindStatusCondition(cr.Status.Conditions, eaglev1alpha1.ConditionDatabaseReady)
	if c == nil || c.Status != metav1.ConditionFalse || c.Reason != "SecretNotFound" {
		t.Errorf("DatabaseReady = %+v", c)
	}
	worker := &appsv1.Deployment{}
	h.mustGet("eagle-worker", worker)
	assertSecretRef(t, envMap(worker.Spec.Template.Spec.Containers[0].Env), "EAGLE_DATABASE_URL", "ext-db", "url")

	if err := h.c.Create(context.Background(), &corev1.Secret{
		ObjectMeta: metav1.ObjectMeta{Name: "ext-db", Namespace: "team"},
		Data:       map[string][]byte{"url": []byte("postgresql+asyncpg://u:p@h:5432/d")},
	}); err != nil {
		t.Fatal(err)
	}
	h.markReady()
	h.reconcile()
	cr = h.cr()
	if !meta.IsStatusConditionTrue(cr.Status.Conditions, eaglev1alpha1.ConditionDatabaseReady) ||
		cr.Status.Phase != eaglev1alpha1.PhaseReady {
		t.Errorf("status = %+v", cr.Status)
	}
}

func TestReconcileSwitchToExternalRemovesManagedObjects(t *testing.T) {
	rp := newRP()
	h := newHarness(t, rp)
	h.reconcile()
	h.mustGet("eagle-db", &appsv1.StatefulSet{})

	cr := h.cr()
	cr.Spec.Database = eaglev1alpha1.DatabaseSpec{
		Managed:  &eaglev1alpha1.ManagedDatabaseSpec{Enabled: ptr.To(false)},
		External: &eaglev1alpha1.ExternalDatabaseSpec{SecretName: "ext-db"},
	}
	if err := h.c.Update(context.Background(), cr); err != nil {
		t.Fatal(err)
	}
	h.reconcile()
	if err := h.get("eagle-db", &appsv1.StatefulSet{}); !apierrors.IsNotFound(err) {
		t.Error("managed StatefulSet should be removed")
	}
	if err := h.get("eagle-database-url", &corev1.Secret{}); !apierrors.IsNotFound(err) {
		t.Error("derived URL secret should be removed")
	}
	if err := h.get("eagle-db", &corev1.Secret{}); err != nil {
		t.Error("generated credentials must be retained")
	}
}

func TestReconcileInvalidSpec(t *testing.T) {
	rp := newRP()
	rp.Spec.Database = eaglev1alpha1.DatabaseSpec{
		Managed:  &eaglev1alpha1.ManagedDatabaseSpec{Enabled: ptr.To(true)},
		External: &eaglev1alpha1.ExternalDatabaseSpec{SecretName: "x"},
	}
	h := newHarness(t, rp)
	if res := h.reconcile(); res.RequeueAfter != 0 {
		t.Error("invalid spec should wait for a spec change, not requeue")
	}
	cr := h.cr()
	c := meta.FindStatusCondition(cr.Status.Conditions, eaglev1alpha1.ConditionReady)
	if cr.Status.Phase != eaglev1alpha1.PhaseFailed || c == nil || c.Reason != "InvalidSpec" {
		t.Errorf("status = %+v", cr.Status)
	}
	if err := h.get("eagle-web", &appsv1.Deployment{}); !apierrors.IsNotFound(err) {
		t.Error("nothing should be created for an invalid spec")
	}
}

func TestReconcileRouteDisabled(t *testing.T) {
	rp := newRP()
	h := newHarness(t, rp)
	h.reconcile()
	h.mustGet("eagle-web", &routev1.Route{})

	cr := h.cr()
	cr.Spec.Route.Enabled = ptr.To(false)
	if err := h.c.Update(context.Background(), cr); err != nil {
		t.Fatal(err)
	}
	h.reconcile()
	if err := h.get("eagle-web", &routev1.Route{}); !apierrors.IsNotFound(err) {
		t.Error("route should be deleted when disabled")
	}
	web := &appsv1.Deployment{}
	h.mustGet("eagle-web", web)
	if _, ok := envMap(web.Spec.Template.Spec.Containers[0].Env)["EAGLE_PUBLIC_URL"]; ok {
		t.Error("EAGLE_PUBLIC_URL must be omitted without a route or host")
	}
}

func TestReconcileWithoutRouteAPI(t *testing.T) {
	rp := newRP()
	h := newHarness(t, rp)
	h.r.RoutesAvailable = false
	h.reconcile()
	if err := h.get("eagle-web", &routev1.Route{}); !apierrors.IsNotFound(err) {
		t.Error("no route should be created when the Route API is unavailable")
	}
}

func TestReconcileMissingCR(t *testing.T) {
	h := newHarness(t, newRP())
	if err := h.c.Delete(context.Background(), h.cr()); err != nil {
		t.Fatal(err)
	}
	h.reconcile()
}

func TestReconcileUsesOperatorDefaultImages(t *testing.T) {
	rp := newRP()
	rp.Spec.Image = ""
	rp.Spec.Runner.Image = ""
	rp.Spec.Database = eaglev1alpha1.DatabaseSpec{}
	h := newHarness(t, rp)
	h.r.Images = DefaultImages{
		App:      "registry.example/eagle:v9",
		Runner:   "registry.example/runner:v9",
		Postgres: "registry.example/postgresql:16",
	}
	h.reconcile()

	web := &appsv1.Deployment{}
	h.mustGet("eagle-web", web)
	if got := web.Spec.Template.Spec.Containers[0].Image; got != "registry.example/eagle:v9" {
		t.Errorf("web image = %q", got)
	}
	if got := web.Spec.Template.Spec.InitContainers[0].Image; got != "registry.example/eagle:v9" {
		t.Errorf("migrate image = %q", got)
	}
	worker := &appsv1.Deployment{}
	h.mustGet("eagle-worker", worker)
	if got := envValue(worker.Spec.Template.Spec.Containers[0].Env, "EAGLE_RUNNER_IMAGE"); got != "registry.example/runner:v9" {
		t.Errorf("EAGLE_RUNNER_IMAGE = %q", got)
	}
	db := &appsv1.StatefulSet{}
	h.mustGet("eagle-db", db)
	if got := db.Spec.Template.Spec.Containers[0].Image; got != "registry.example/postgresql:16" {
		t.Errorf("postgres image = %q", got)
	}

	// Defaults are applied in memory only: the stored spec stays empty, so an
	// operator upgrade moves the instance to the new release's images.
	cr := h.cr()
	if cr.Spec.Image != "" || cr.Spec.Runner.Image != "" || cr.Spec.Database.Managed != nil {
		t.Errorf("stored spec was modified: %+v", cr.Spec)
	}
}

func TestReconcileExplicitImagesWinOverDefaults(t *testing.T) {
	rp := newRP()
	rp.Spec.Database = eaglev1alpha1.DatabaseSpec{
		Managed: &eaglev1alpha1.ManagedDatabaseSpec{Image: "mine/postgres:1"},
	}
	h := newHarness(t, rp)
	h.r.Images = DefaultImages{App: "default/app", Runner: "default/runner", Postgres: "default/pg"}
	h.reconcile()

	web := &appsv1.Deployment{}
	h.mustGet("eagle-web", web)
	if got := web.Spec.Template.Spec.Containers[0].Image; got != rp.Spec.Image {
		t.Errorf("web image = %q, want %q", got, rp.Spec.Image)
	}
	db := &appsv1.StatefulSet{}
	h.mustGet("eagle-db", db)
	if got := db.Spec.Template.Spec.Containers[0].Image; got != "mine/postgres:1" {
		t.Errorf("postgres image = %q", got)
	}
}

func TestReconcileNoImageAndNoDefaultFails(t *testing.T) {
	rp := newRP()
	rp.Spec.Image = ""
	h := newHarness(t, rp)
	h.reconcile()
	cr := h.cr()
	c := meta.FindStatusCondition(cr.Status.Conditions, eaglev1alpha1.ConditionReady)
	if cr.Status.Phase != eaglev1alpha1.PhaseFailed || c == nil || !bytes.Contains([]byte(c.Message), []byte("RELATED_IMAGE_EAGLE")) {
		t.Errorf("status = %+v", cr.Status)
	}
}

func envValue(env []corev1.EnvVar, name string) string {
	for _, e := range env {
		if e.Name == name {
			return e.Value
		}
	}
	return ""
}

func TestReconcileKeepsRolloutRestart(t *testing.T) {
	h := newHarness(t, newRP())
	h.reconcile()
	for _, name := range []string{"eagle-web", "eagle-worker"} {
		dep := &appsv1.Deployment{}
		h.mustGet(name, dep)
		if dep.Spec.Template.Annotations == nil {
			dep.Spec.Template.Annotations = map[string]string{}
		}
		// What `oc rollout restart` does.
		dep.Spec.Template.Annotations[RestartedAtAnnotation] = "2026-11-04T15:00:00Z"
		if err := h.c.Update(context.Background(), dep); err != nil {
			t.Fatal(err)
		}
	}
	sts := &appsv1.StatefulSet{}
	h.mustGet("eagle-db", sts)
	sts.Spec.Template.Annotations = map[string]string{RestartedAtAnnotation: "2026-11-04T15:00:00Z"}
	if err := h.c.Update(context.Background(), sts); err != nil {
		t.Fatal(err)
	}

	h.reconcile()
	for _, name := range []string{"eagle-web", "eagle-worker"} {
		dep := &appsv1.Deployment{}
		h.mustGet(name, dep)
		if got := dep.Spec.Template.Annotations[RestartedAtAnnotation]; got != "2026-11-04T15:00:00Z" {
			t.Errorf("%s: reconcile undid the rollout restart (annotation %q)", name, got)
		}
		if dep.Spec.Template.Annotations["kubectl.kubernetes.io/default-container"] == "" &&
			len(dep.Spec.Template.Spec.Containers) == 0 {
			t.Errorf("%s: template lost its desired content", name)
		}
	}
	h.mustGet("eagle-db", sts)
	if got := sts.Spec.Template.Annotations[RestartedAtAnnotation]; got != "2026-11-04T15:00:00Z" {
		t.Errorf("eagle-db: reconcile undid the rollout restart (annotation %q)", got)
	}
}
