package controller

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/base64"
	"net/url"
	"reflect"
	"sort"
	"strings"
	"testing"

	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	networkingv1 "k8s.io/api/networking/v1"
	rbacv1 "k8s.io/api/rbac/v1"
	"k8s.io/apimachinery/pkg/api/resource"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"
	"k8s.io/utils/ptr"
	"sigs.k8s.io/controller-runtime/pkg/client/fake"

	eaglev1alpha1 "github.com/specgetti/eagle/operator/api/v1alpha1"
)

func newRP() *eaglev1alpha1.ReviewPlatform {
	return &eaglev1alpha1.ReviewPlatform{
		ObjectMeta: metav1.ObjectMeta{Name: "eagle", Namespace: "team", UID: types.UID("uid-1"), Generation: 1},
		Spec: eaglev1alpha1.ReviewPlatformSpec{
			Image:  "quay.io/x/eagle:1",
			Runner: eaglev1alpha1.RunnerSpec{Image: "quay.io/x/runner:1"},
		},
	}
}

func envMap(env []corev1.EnvVar) map[string]corev1.EnvVar {
	m := map[string]corev1.EnvVar{}
	for _, e := range env {
		m[e.Name] = e
	}
	return m
}

func assertSecretRef(t *testing.T, env map[string]corev1.EnvVar, name, secret, key string) {
	t.Helper()
	e, ok := env[name]
	if !ok {
		t.Fatalf("env %s missing", name)
	}
	if e.ValueFrom == nil || e.ValueFrom.SecretKeyRef == nil {
		t.Fatalf("env %s is not a secretKeyRef: %+v", name, e)
	}
	if e.ValueFrom.SecretKeyRef.Name != secret || e.ValueFrom.SecretKeyRef.Key != key {
		t.Fatalf("env %s refs %s/%s, want %s/%s", name, e.ValueFrom.SecretKeyRef.Name, e.ValueFrom.SecretKeyRef.Key, secret, key)
	}
}

func assertValue(t *testing.T, env map[string]corev1.EnvVar, name, want string) {
	t.Helper()
	e, ok := env[name]
	if !ok {
		t.Fatalf("env %s missing", name)
	}
	if e.Value != want {
		t.Fatalf("env %s = %q, want %q", name, e.Value, want)
	}
}

func TestCommonEnvManagedDefaults(t *testing.T) {
	rp := newRP()
	env := envMap(CommonEnv(rp, "https://eagle.apps.example.com"))
	assertSecretRef(t, env, "EAGLE_DATABASE_URL", "eagle-database-url", "url")
	assertSecretRef(t, env, "EAGLE_SECRET_KEY", "eagle-keys", "EAGLE_SECRET_KEY")
	assertSecretRef(t, env, "EAGLE_ENCRYPTION_KEY", "eagle-keys", "EAGLE_ENCRYPTION_KEY")
	assertValue(t, env, "EAGLE_BOOTSTRAP_ADMIN_USERNAME", "admin")
	assertValue(t, env, "EAGLE_AUTO_CREATE_SCHEMA", "false")
	assertValue(t, env, "EAGLE_COOKIE_SECURE", "true")
	assertValue(t, env, "EAGLE_PUBLIC_URL", "https://eagle.apps.example.com")
	if _, ok := env["EAGLE_BOOTSTRAP_ADMIN_PASSWORD"]; ok {
		t.Error("bootstrap password must be absent without passwordSecretRef")
	}
	for _, workerOnly := range []string{"EAGLE_EXECUTOR", "EAGLE_RUNNER_IMAGE", "EAGLE_K8S_NAMESPACE", "EAGLE_INSTANCE_NAME"} {
		if _, ok := env[workerOnly]; ok {
			t.Errorf("%s must only be set on the worker", workerOnly)
		}
	}
}

func TestCommonEnvExternalAndOverrides(t *testing.T) {
	rp := newRP()
	rp.Spec.Database.External = &eaglev1alpha1.ExternalDatabaseSpec{SecretName: "my-db"}
	rp.Spec.Secrets.ExistingSecret = "my-keys"
	rp.Spec.BootstrapAdmin = eaglev1alpha1.BootstrapAdminSpec{
		Username:          "root",
		PasswordSecretRef: &eaglev1alpha1.SecretKeyRef{Name: "admin-pw"},
	}
	env := envMap(CommonEnv(rp, ""))
	assertSecretRef(t, env, "EAGLE_DATABASE_URL", "my-db", "url")
	assertSecretRef(t, env, "EAGLE_SECRET_KEY", "my-keys", "EAGLE_SECRET_KEY")
	assertSecretRef(t, env, "EAGLE_BOOTSTRAP_ADMIN_PASSWORD", "admin-pw", "password")
	assertValue(t, env, "EAGLE_BOOTSTRAP_ADMIN_USERNAME", "root")
	if _, ok := env["EAGLE_PUBLIC_URL"]; ok {
		t.Error("EAGLE_PUBLIC_URL must be omitted when unknown")
	}

	rp.Spec.Database.External.Key = "dsn"
	rp.Spec.BootstrapAdmin.PasswordSecretRef.Key = "pw"
	env = envMap(CommonEnv(rp, ""))
	assertSecretRef(t, env, "EAGLE_DATABASE_URL", "my-db", "dsn")
	assertSecretRef(t, env, "EAGLE_BOOTSTRAP_ADMIN_PASSWORD", "admin-pw", "pw")
}

func TestWorkerEnv(t *testing.T) {
	rp := newRP()
	env := envMap(WorkerEnv(rp, "https://x"))
	assertValue(t, env, "EAGLE_EXECUTOR", "kubernetes")
	assertValue(t, env, "EAGLE_INSTANCE_NAME", "eagle")
	assertValue(t, env, "EAGLE_RUNNER_IMAGE", "quay.io/x/runner:1")
	assertValue(t, env, "EAGLE_RUNNER_SERVICE_ACCOUNT", "eagle-runner")
	assertValue(t, env, "EAGLE_CALLBACK_BASE_URL", "http://eagle-web.team.svc:8000")
	assertValue(t, env, "EAGLE_RUNNER_CPU_LIMIT", "2")
	assertValue(t, env, "EAGLE_RUNNER_MEMORY_LIMIT", "4Gi")
	assertValue(t, env, "EAGLE_PUBLIC_URL", "https://x")
	ns := env["EAGLE_K8S_NAMESPACE"]
	if ns.ValueFrom == nil || ns.ValueFrom.FieldRef == nil || ns.ValueFrom.FieldRef.FieldPath != "metadata.namespace" {
		t.Fatalf("EAGLE_K8S_NAMESPACE must use the downward API: %+v", ns)
	}

	rp.Spec.Runner.Resources.Limits = corev1.ResourceList{
		corev1.ResourceCPU: resource.MustParse("1500m"), corev1.ResourceMemory: resource.MustParse("8Gi"),
	}
	env = envMap(WorkerEnv(rp, ""))
	assertValue(t, env, "EAGLE_RUNNER_CPU_LIMIT", "1500m")
	assertValue(t, env, "EAGLE_RUNNER_MEMORY_LIMIT", "8Gi")

	// Env var names must be unique within the container.
	seen := map[string]bool{}
	for _, e := range WorkerEnv(rp, "https://x") {
		if seen[e.Name] {
			t.Errorf("duplicate env %s", e.Name)
		}
		seen[e.Name] = true
	}
}

func TestWebDeployment(t *testing.T) {
	rp := newRP()
	rp.Spec.Replicas = ptr.To[int32](2)
	d := BuildWebDeployment(rp, "https://x")
	if *d.Spec.Replicas != 2 {
		t.Errorf("replicas = %d", *d.Spec.Replicas)
	}
	pod := d.Spec.Template.Spec
	if pod.ServiceAccountName != "eagle-web" || pod.AutomountServiceAccountToken == nil || *pod.AutomountServiceAccountToken {
		t.Error("web must use eagle-web SA without a token")
	}
	if len(pod.InitContainers) != 1 || !reflect.DeepEqual(pod.InitContainers[0].Command, []string{"eagle", "migrate"}) {
		t.Fatalf("migrate initContainer missing: %+v", pod.InitContainers)
	}
	if pod.InitContainers[0].Image != rp.Spec.Image {
		t.Error("migrate must use the app image")
	}
	c := pod.Containers[0]
	if c.Command != nil {
		t.Error("web must run the image default CMD")
	}
	if c.Ports[0].ContainerPort != 8000 {
		t.Error("web port must be 8000")
	}
	if c.LivenessProbe.HTTPGet.Path != "/healthz" || c.ReadinessProbe.HTTPGet.Path != "/readyz" {
		t.Error("probe paths")
	}
	if d.Spec.Template.Labels[LabelComponent] != ComponentWeb || d.Spec.Template.Labels[LabelInstance] != "eagle" {
		t.Error("web pod labels must carry component eagle-web and instance")
	}
	for k, v := range d.Spec.Selector.MatchLabels {
		if d.Spec.Template.Labels[k] != v {
			t.Errorf("selector label %s not on template", k)
		}
	}
	if c.SecurityContext == nil || *c.SecurityContext.AllowPrivilegeEscalation {
		t.Error("restricted security context expected")
	}
}

func TestWorkerDeployment(t *testing.T) {
	rp := newRP()
	rp.Spec.WorkerReplicas = ptr.To[int32](3)
	d := BuildWorkerDeployment(rp, "")
	if *d.Spec.Replicas != 3 {
		t.Errorf("replicas = %d", *d.Spec.Replicas)
	}
	pod := d.Spec.Template.Spec
	if pod.ServiceAccountName != "eagle-worker" {
		t.Error("worker SA")
	}
	if !reflect.DeepEqual(pod.Containers[0].Command, []string{"eagle", "worker"}) {
		t.Error("worker command")
	}
	if len(pod.Containers[0].Ports) != 0 || pod.Containers[0].LivenessProbe != nil {
		t.Error("worker has no HTTP port or probes")
	}
	if len(pod.InitContainers) != 0 {
		t.Error("migrations only run in the web Deployment")
	}
}

func TestWorkerRBAC(t *testing.T) {
	rp := newRP()
	want := map[string][]string{
		"batch/jobs": {"create", "delete", "get", "list", "watch"},
		"/secrets":   {"create", "delete", "get", "patch"},
		"/pods":      {"get", "list"},
		"/pods/log":  {"get"},
	}
	got := map[string][]string{}
	for _, r := range BuildWorkerRole(rp).Rules {
		for _, g := range r.APIGroups {
			for _, res := range r.Resources {
				v := append([]string(nil), r.Verbs...)
				sort.Strings(v)
				got[g+"/"+res] = v
			}
		}
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("worker rules = %v, want %v", got, want)
	}
	rb := BuildWorkerRoleBinding(rp)
	if rb.RoleRef.Name != "eagle-worker" || rb.RoleRef.Kind != "Role" {
		t.Error("roleRef")
	}
	if len(rb.Subjects) != 1 || rb.Subjects[0].Name != "eagle-worker" || rb.Subjects[0].Namespace != "team" ||
		rb.Subjects[0].Kind != rbacv1.ServiceAccountKind {
		t.Errorf("subjects = %+v", rb.Subjects)
	}

	sas := BuildServiceAccounts(rp)
	byName := map[string]*corev1.ServiceAccount{}
	for _, sa := range sas {
		byName[sa.Name] = sa
	}
	if len(byName) != 3 {
		t.Fatalf("want 3 service accounts, got %v", byName)
	}
	runner := byName["eagle-runner"]
	if runner == nil || runner.AutomountServiceAccountToken == nil || *runner.AutomountServiceAccountToken {
		t.Error("runner SA must disable token automount")
	}
	if byName["eagle-web"] == nil || byName["eagle-worker"] == nil {
		t.Error("web and worker SAs")
	}
}

func TestRunnerNetworkPolicy(t *testing.T) {
	rp := newRP()
	np := BuildRunnerNetworkPolicy(rp)
	sel := np.Spec.PodSelector.MatchLabels
	if !reflect.DeepEqual(sel, map[string]string{LabelComponent: "eagle-runner", LabelInstance: "eagle"}) {
		t.Errorf("pod selector = %v", sel)
	}
	if !reflect.DeepEqual(np.Spec.PolicyTypes, []networkingv1.PolicyType{"Ingress", "Egress"}) {
		t.Errorf("policy types = %v", np.Spec.PolicyTypes)
	}
	if len(np.Spec.Ingress) != 0 {
		t.Error("runner ingress must be denied")
	}
	if len(np.Spec.Egress) != 3 {
		t.Fatalf("want 3 egress rules, got %d", len(np.Spec.Egress))
	}

	dns := np.Spec.Egress[0]
	if len(dns.To) != 0 {
		t.Error("DNS rule must allow any destination")
	}
	ports := map[string]bool{}
	for _, p := range dns.Ports {
		ports[string(*p.Protocol)+"/"+p.Port.String()] = true
	}
	for _, want := range []string{"UDP/53", "TCP/53"} {
		if !ports[want] {
			t.Errorf("DNS rule missing %s", want)
		}
	}

	web := np.Spec.Egress[1]
	if len(web.To) != 1 || web.To[0].PodSelector == nil ||
		!reflect.DeepEqual(web.To[0].PodSelector.MatchLabels, map[string]string{LabelComponent: "eagle-web", LabelInstance: "eagle"}) {
		t.Errorf("web egress peer = %+v", web.To)
	}
	if len(web.Ports) != 1 || web.Ports[0].Port.IntValue() != 8000 || *web.Ports[0].Protocol != corev1.ProtocolTCP {
		t.Errorf("web egress ports = %+v", web.Ports)
	}

	ext := np.Spec.Egress[2]
	if len(ext.To) != 1 || ext.To[0].IPBlock == nil || ext.To[0].IPBlock.CIDR != "0.0.0.0/0" {
		t.Fatalf("external egress = %+v", ext.To)
	}
	if !reflect.DeepEqual(ext.To[0].IPBlock.Except, eaglev1alpha1.DefaultBlockedCIDRs) {
		t.Errorf("except = %v", ext.To[0].IPBlock.Except)
	}

	rp.Spec.Runner.BlockedCIDRs = []string{"100.64.0.0/10"}
	np = BuildRunnerNetworkPolicy(rp)
	if !reflect.DeepEqual(np.Spec.Egress[2].To[0].IPBlock.Except, []string{"100.64.0.0/10"}) {
		t.Error("custom blockedCIDRs not applied")
	}
}

func TestWebAndPostgresNetworkPolicies(t *testing.T) {
	rp := newRP()
	web := BuildWebNetworkPolicy(rp)
	if !reflect.DeepEqual(web.Spec.PolicyTypes, []networkingv1.PolicyType{"Ingress"}) {
		t.Error("web policy restricts ingress only")
	}
	r := web.Spec.Ingress[0]
	if r.Ports[0].Port.IntValue() != 8000 {
		t.Error("web ingress port")
	}
	var router, sameNS bool
	for _, p := range r.From {
		if p.NamespaceSelector != nil && p.NamespaceSelector.MatchLabels["network.openshift.io/policy-group"] == "ingress" {
			router = true
		}
		if p.PodSelector != nil && p.NamespaceSelector == nil && len(p.PodSelector.MatchLabels) == 0 {
			sameNS = true
		}
	}
	if !router || !sameNS {
		t.Errorf("web ingress peers router=%v sameNS=%v", router, sameNS)
	}

	db := BuildPostgresNetworkPolicy(rp)
	peer := db.Spec.Ingress[0].From[0].PodSelector
	if peer.MatchLabels[LabelInstance] != "eagle" || !reflect.DeepEqual(peer.MatchExpressions[0].Values, []string{"eagle-web", "eagle-worker"}) {
		t.Errorf("postgres peers = %+v", peer)
	}
	if db.Spec.Ingress[0].Ports[0].Port.IntValue() != 5432 {
		t.Error("postgres port")
	}
}

func TestPostgresStatefulSet(t *testing.T) {
	rp := newRP()
	sc := "fast"
	rp.Spec.Database.Managed = &eaglev1alpha1.ManagedDatabaseSpec{
		StorageSize: ptr.To(resource.MustParse("20Gi")), StorageClassName: &sc,
	}
	sts := BuildPostgresStatefulSet(rp)
	if *sts.Spec.Replicas != 1 || sts.Spec.ServiceName != "eagle-db" {
		t.Error("replicas/serviceName")
	}
	if sts.Spec.PersistentVolumeClaimRetentionPolicy.WhenDeleted != appsv1.RetainPersistentVolumeClaimRetentionPolicyType {
		t.Error("PVC must be retained on delete")
	}
	c := sts.Spec.Template.Spec.Containers[0]
	if c.Image != eaglev1alpha1.DefaultPostgresImage {
		t.Errorf("image = %s", c.Image)
	}
	env := envMap(c.Env)
	for _, k := range []string{"POSTGRESQL_USER", "POSTGRESQL_PASSWORD", "POSTGRESQL_DATABASE"} {
		assertSecretRef(t, env, k, "eagle-db", k)
	}
	vct := sts.Spec.VolumeClaimTemplates[0]
	if q := vct.Spec.Resources.Requests[corev1.ResourceStorage]; q.String() != "20Gi" {
		t.Errorf("storage = %s", q.String())
	}
	if *vct.Spec.StorageClassName != "fast" {
		t.Error("storage class")
	}
	if PostgresPVCName(rp) != "data-eagle-db-0" {
		t.Errorf("pvc name = %s", PostgresPVCName(rp))
	}
}

func TestMutateStatefulSetKeepsImmutableFields(t *testing.T) {
	rp := newRP()
	live := BuildPostgresStatefulSet(rp)
	live.CreationTimestamp = metav1.Now()
	rp.Spec.Database.Managed = &eaglev1alpha1.ManagedDatabaseSpec{StorageSize: ptr.To(resource.MustParse("50Gi")), Image: "pg:17"}
	MutateStatefulSet(live, BuildPostgresStatefulSet(rp))
	if q := live.Spec.VolumeClaimTemplates[0].Spec.Resources.Requests[corev1.ResourceStorage]; q.String() != "5Gi" {
		t.Error("volumeClaimTemplates must not change after creation")
	}
	if live.Spec.Template.Spec.Containers[0].Image != "pg:17" {
		t.Error("template should update")
	}
}

func TestRouteAndPublicURL(t *testing.T) {
	rp := newRP()
	r := BuildRoute(rp)
	if r.Spec.TLS.Termination != "edge" || r.Spec.TLS.InsecureEdgeTerminationPolicy != "Redirect" {
		t.Errorf("tls = %+v", r.Spec.TLS)
	}
	if r.Spec.To.Name != "eagle-web" || r.Spec.Host != "" {
		t.Error("route target/host")
	}

	// An assigned host must survive reconciles that do not set one.
	live := BuildRoute(rp)
	live.Spec.Host = "eagle-team.apps.example.com"
	MutateRoute(live, BuildRoute(rp))
	if live.Spec.Host != "eagle-team.apps.example.com" {
		t.Error("assigned host was cleared")
	}

	if got := PublicURL(rp, "eagle-team.apps.example.com"); got != "https://eagle-team.apps.example.com" {
		t.Errorf("PublicURL = %s", got)
	}
	if got := PublicURL(rp, ""); got != "" {
		t.Errorf("PublicURL without host = %q", got)
	}
	rp.Spec.Route.Enabled = ptr.To(false)
	rp.Spec.Route.Host = "eagle.example.com"
	if got := PublicURL(rp, "ignored"); got != "https://eagle.example.com" {
		t.Errorf("PublicURL with route disabled = %s", got)
	}
}

func TestGeneratedKeys(t *testing.T) {
	rp := newRP()
	s, err := BuildKeysSecret(rp, rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	if s.Name != "eagle-keys" || len(s.OwnerReferences) != 0 {
		t.Error("keys secret must be <name>-keys without owner references")
	}
	fernet, err := base64.URLEncoding.DecodeString(string(s.Data[KeyEncryptionKey]))
	if err != nil || len(fernet) != 32 {
		t.Fatalf("encryption key must be 32 bytes urlsafe base64: len=%d err=%v", len(fernet), err)
	}
	if len(s.Data[KeySecretKey]) < 32 {
		t.Error("secret key too short")
	}

	if _, err := BuildKeysSecret(rp, bytes.NewReader(nil)); err == nil {
		t.Error("expected an error when entropy is unavailable")
	}

	pw, err := RandomAlnum(rand.Reader, 64)
	if err != nil || len(pw) != 64 || strings.Trim(pw, alnum) != "" {
		t.Errorf("RandomAlnum = %q, %v", pw, err)
	}
}

func TestDatabaseURL(t *testing.T) {
	rp := newRP()
	creds, err := BuildDBCredentialsSecret(rp, rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	if creds.Name != "eagle-db" || len(creds.OwnerReferences) != 0 {
		t.Error("db secret must be <name>-db without owner references")
	}
	s, err := BuildDatabaseURLSecret(rp, creds)
	if err != nil {
		t.Fatal(err)
	}
	u, err := url.Parse(string(s.Data["url"]))
	if err != nil {
		t.Fatal(err)
	}
	pw, _ := u.User.Password()
	if u.Scheme != "postgresql+asyncpg" || u.Host != "eagle-db.team.svc:5432" || u.Path != "/eagle" ||
		u.User.Username() != "eagle" || pw != string(creds.Data[KeyPostgresPassword]) {
		t.Errorf("url = %s", u)
	}

	// Special characters in user supplied credentials must be escaped.
	creds.Data[KeyPostgresPassword] = []byte("p@ss:w/rd")
	s, _ = BuildDatabaseURLSecret(rp, creds)
	u, _ = url.Parse(string(s.Data["url"]))
	if pw, _ := u.User.Password(); pw != "p@ss:w/rd" {
		t.Errorf("password round trip = %q", pw)
	}

	delete(creds.Data, KeyPostgresUser)
	if _, err := BuildDatabaseURLSecret(rp, creds); err == nil {
		t.Error("expected an error for incomplete credentials")
	}
}

func TestEnsureSecretOnceNeverOverwrites(t *testing.T) {
	ctx := context.Background()
	rp := newRP()
	c := fake.NewClientBuilder().Build()

	first, err := BuildKeysSecret(rp, rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	got, created, err := EnsureSecretOnce(ctx, c, c, first)
	if err != nil || !created {
		t.Fatalf("first call: created=%v err=%v", created, err)
	}
	original := got.Data[KeyEncryptionKey]

	second, _ := BuildKeysSecret(rp, rand.Reader)
	if bytes.Equal(second.Data[KeyEncryptionKey], original) {
		t.Fatal("test needs two different candidate keys")
	}
	got, created, err = EnsureSecretOnce(ctx, c, c, second)
	if err != nil || created {
		t.Fatalf("second call: created=%v err=%v", created, err)
	}
	if !bytes.Equal(got.Data[KeyEncryptionKey], original) {
		t.Fatal("existing key was replaced")
	}
	stored := &corev1.Secret{}
	if err := c.Get(ctx, types.NamespacedName{Namespace: "team", Name: "eagle-keys"}, stored); err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(stored.Data[KeyEncryptionKey], original) {
		t.Fatal("stored key changed")
	}
}

func TestSSOEnvAbsentByDefault(t *testing.T) {
	rp := newRP()
	if env := SSOEnv(rp); env != nil {
		t.Fatalf("no SSO env expected without spec.sso: %+v", env)
	}
	web := envMap(BuildWebDeployment(rp, "").Spec.Template.Spec.Containers[0].Env)
	if _, ok := web["EAGLE_LOCAL_LOGIN_ENABLED"]; ok {
		t.Error("EAGLE_LOCAL_LOGIN_ENABLED must be absent without spec.sso")
	}
}

func TestSSOEnvIncompleteOnlyLocalLogin(t *testing.T) {
	rp := newRP()
	rp.Spec.SSO = &eaglev1alpha1.SSOSpec{Issuer: "https://idp.example.com", LocalLoginEnabled: ptr.To(false)}
	env := envMap(SSOEnv(rp))
	if len(env) != 1 {
		t.Fatalf("only EAGLE_LOCAL_LOGIN_ENABLED expected without clientId, got %v", env)
	}
	assertValue(t, env, "EAGLE_LOCAL_LOGIN_ENABLED", "false")
}

func TestSSOEnvDefaults(t *testing.T) {
	rp := newRP()
	rp.Spec.SSO = &eaglev1alpha1.SSOSpec{
		Issuer:          "https://sso.redhat.com/auth/realms/x",
		ClientID:        "eagle",
		ClientSecretRef: &eaglev1alpha1.SecretKeyRef{Name: "eagle-oidc"},
	}
	env := envMap(SSOEnv(rp))
	assertValue(t, env, "EAGLE_OIDC_ISSUER", "https://sso.redhat.com/auth/realms/x")
	assertValue(t, env, "EAGLE_OIDC_CLIENT_ID", "eagle")
	assertSecretRef(t, env, "EAGLE_OIDC_CLIENT_SECRET", "eagle-oidc", "clientSecret")
	assertValue(t, env, "EAGLE_OIDC_SCOPES", "openid email profile")
	assertValue(t, env, "EAGLE_OIDC_DEFAULT_ROLE", "viewer")
	assertValue(t, env, "EAGLE_OIDC_ADMIN_GROUPS", "[]")
	assertValue(t, env, "EAGLE_OIDC_MEMBER_GROUPS", "[]")
	assertValue(t, env, "EAGLE_OIDC_VIEWER_GROUPS", "[]")
	assertValue(t, env, "EAGLE_LOCAL_LOGIN_ENABLED", "true")
	for _, k := range []string{"EAGLE_OIDC_BUTTON_LABEL", "EAGLE_OIDC_USERNAME_CLAIM", "EAGLE_OIDC_GROUPS_CLAIM"} {
		if _, ok := env[k]; ok {
			t.Errorf("%s should be omitted when unset", k)
		}
	}
}

func TestSSOEnvFull(t *testing.T) {
	rp := newRP()
	rp.Spec.SSO = &eaglev1alpha1.SSOSpec{
		Issuer:            "https://idp",
		ClientID:          "eagle",
		ClientSecretRef:   &eaglev1alpha1.SecretKeyRef{Name: "oidc", Key: "secret"},
		Scopes:            "openid groups",
		ButtonLabel:       "Red Hat SSO",
		UsernameClaim:     "preferred_username",
		GroupsClaim:       "groups",
		AdminGroups:       []string{"eagle-admins", "ops"},
		MemberGroups:      []string{"dev \"team\""},
		DefaultRole:       "none",
		LocalLoginEnabled: ptr.To(false),
	}
	env := envMap(SSOEnv(rp))
	assertSecretRef(t, env, "EAGLE_OIDC_CLIENT_SECRET", "oidc", "secret")
	assertValue(t, env, "EAGLE_OIDC_SCOPES", "openid groups")
	assertValue(t, env, "EAGLE_OIDC_BUTTON_LABEL", "Red Hat SSO")
	assertValue(t, env, "EAGLE_OIDC_USERNAME_CLAIM", "preferred_username")
	assertValue(t, env, "EAGLE_OIDC_GROUPS_CLAIM", "groups")
	assertValue(t, env, "EAGLE_OIDC_ADMIN_GROUPS", `["eagle-admins","ops"]`)
	assertValue(t, env, "EAGLE_OIDC_MEMBER_GROUPS", `["dev \"team\""]`)
	assertValue(t, env, "EAGLE_OIDC_VIEWER_GROUPS", "[]")
	assertValue(t, env, "EAGLE_OIDC_DEFAULT_ROLE", "none")
	assertValue(t, env, "EAGLE_LOCAL_LOGIN_ENABLED", "false")
}

func TestSSOEnvWebContainerOnly(t *testing.T) {
	rp := newRP()
	rp.Spec.SSO = &eaglev1alpha1.SSOSpec{Issuer: "https://idp", ClientID: "eagle"}
	web := BuildWebDeployment(rp, "https://x").Spec.Template.Spec
	if _, ok := envMap(web.Containers[0].Env)["EAGLE_OIDC_ISSUER"]; !ok {
		t.Error("web container must get SSO env")
	}
	if _, ok := envMap(web.InitContainers[0].Env)["EAGLE_OIDC_ISSUER"]; ok {
		t.Error("migrate initContainer must not get SSO env")
	}
	worker := BuildWorkerDeployment(rp, "https://x").Spec.Template.Spec.Containers[0]
	for _, e := range worker.Env {
		if strings.HasPrefix(e.Name, "EAGLE_OIDC_") || e.Name == "EAGLE_LOCAL_LOGIN_ENABLED" {
			t.Errorf("worker must not get %s", e.Name)
		}
	}
}
