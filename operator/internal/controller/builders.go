package controller

import (
	"encoding/json"
	"fmt"
	"strconv"

	routev1 "github.com/openshift/api/route/v1"
	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	networkingv1 "k8s.io/api/networking/v1"
	rbacv1 "k8s.io/api/rbac/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/util/intstr"
	"k8s.io/utils/ptr"

	eaglev1alpha1 "github.com/hmoghani/eagle/operator/api/v1alpha1"
)

// Builders in this file are pure: they take the CR (plus already resolved
// inputs) and return the desired object. The controller copies the relevant
// fields into the live object inside controllerutil.CreateOrUpdate.

// PublicURL returns the external URL. routeHost is the host assigned to the
// live Route (may be empty if no Route exists or it has no host yet).
func PublicURL(rp *eaglev1alpha1.ReviewPlatform, routeHost string) string {
	host := rp.Spec.Route.Host
	if rp.Spec.RouteEnabled() && routeHost != "" {
		host = routeHost
	}
	if host == "" {
		return ""
	}
	return "https://" + host
}

func secretEnv(name, secret, key string, optional bool) corev1.EnvVar {
	ref := &corev1.SecretKeySelector{
		LocalObjectReference: corev1.LocalObjectReference{Name: secret},
		Key:                  key,
	}
	if optional {
		ref.Optional = ptr.To(true)
	}
	return corev1.EnvVar{Name: name, ValueFrom: &corev1.EnvVarSource{SecretKeyRef: ref}}
}

// DatabaseURLSource returns the Secret name and key that hold EAGLE_DATABASE_URL.
func DatabaseURLSource(rp *eaglev1alpha1.ReviewPlatform) (secret, key string) {
	if rp.Spec.ManagedDatabaseEnabled() {
		return DatabaseURLSecretName(rp), KeyDatabaseURL
	}
	return rp.Spec.Database.External.SecretName, rp.Spec.ExternalDatabaseKey()
}

// CommonEnv returns the env vars shared by the web, migrate and worker containers.
func CommonEnv(rp *eaglev1alpha1.ReviewPlatform, publicURL string) []corev1.EnvVar {
	dbSecret, dbKey := DatabaseURLSource(rp)
	keys := KeysSecretName(rp)
	env := []corev1.EnvVar{
		secretEnv("EAGLE_DATABASE_URL", dbSecret, dbKey, false),
		secretEnv("EAGLE_SECRET_KEY", keys, KeySecretKey, false),
		secretEnv("EAGLE_ENCRYPTION_KEY", keys, KeyEncryptionKey, false),
		{Name: "EAGLE_BOOTSTRAP_ADMIN_USERNAME", Value: rp.Spec.BootstrapAdminUsername()},
	}
	if ref := rp.Spec.BootstrapAdmin.PasswordSecretRef; ref != nil {
		key := ref.Key
		if key == "" {
			key = eaglev1alpha1.DefaultPasswordSecretKey
		}
		env = append(env, secretEnv("EAGLE_BOOTSTRAP_ADMIN_PASSWORD", ref.Name, key, false))
	}
	env = append(env,
		corev1.EnvVar{Name: "EAGLE_AUTO_CREATE_SCHEMA", Value: "false"},
		corev1.EnvVar{Name: "EAGLE_COOKIE_SECURE", Value: "true"},
	)
	if publicURL != "" {
		env = append(env, corev1.EnvVar{Name: "EAGLE_PUBLIC_URL", Value: publicURL})
	}
	return env
}

// jsonList renders a string list as a JSON array, the format pydantic-settings
// parses list settings from. A nil list renders as [].
func jsonList(v []string) string {
	if v == nil {
		v = []string{}
	}
	b, _ := json.Marshal(v) // marshaling []string cannot fail
	return string(b)
}

// SSOEnv returns the OIDC env vars for the web container. The OIDC settings
// are emitted only when issuer and clientId are both set;
// EAGLE_LOCAL_LOGIN_ENABLED is emitted whenever spec.sso is present.
func SSOEnv(rp *eaglev1alpha1.ReviewPlatform) []corev1.EnvVar {
	sso := rp.Spec.SSO
	if sso == nil {
		return nil
	}
	var env []corev1.EnvVar
	add := func(name, value string) { env = append(env, corev1.EnvVar{Name: name, Value: value}) }
	if rp.Spec.SSOEnabled() {
		add("EAGLE_OIDC_ISSUER", sso.Issuer)
		add("EAGLE_OIDC_CLIENT_ID", sso.ClientID)
		if ref := sso.ClientSecretRef; ref != nil {
			key := ref.Key
			if key == "" {
				key = eaglev1alpha1.DefaultClientSecretKey
			}
			env = append(env, secretEnv("EAGLE_OIDC_CLIENT_SECRET", ref.Name, key, false))
		}
		scopes := sso.Scopes
		if scopes == "" {
			scopes = eaglev1alpha1.DefaultOIDCScopes
		}
		add("EAGLE_OIDC_SCOPES", scopes)
		if sso.ButtonLabel != "" {
			add("EAGLE_OIDC_BUTTON_LABEL", sso.ButtonLabel)
		}
		if sso.UsernameClaim != "" {
			add("EAGLE_OIDC_USERNAME_CLAIM", sso.UsernameClaim)
		}
		if sso.GroupsClaim != "" {
			add("EAGLE_OIDC_GROUPS_CLAIM", sso.GroupsClaim)
		}
		add("EAGLE_OIDC_ADMIN_GROUPS", jsonList(sso.AdminGroups))
		add("EAGLE_OIDC_MEMBER_GROUPS", jsonList(sso.MemberGroups))
		add("EAGLE_OIDC_VIEWER_GROUPS", jsonList(sso.ViewerGroups))
		role := sso.DefaultRole
		if role == "" {
			role = eaglev1alpha1.DefaultOIDCRole
		}
		add("EAGLE_OIDC_DEFAULT_ROLE", role)
	}
	add("EAGLE_LOCAL_LOGIN_ENABLED", strconv.FormatBool(sso.LocalLoginEnabled == nil || *sso.LocalLoginEnabled))
	return env
}

// WebEnv returns the env vars for the web container (common plus SSO).
func WebEnv(rp *eaglev1alpha1.ReviewPlatform, publicURL string) []corev1.EnvVar {
	return append(CommonEnv(rp, publicURL), SSOEnv(rp)...)
}

// WorkerEnv returns the env vars for the worker container.
func WorkerEnv(rp *eaglev1alpha1.ReviewPlatform, publicURL string) []corev1.EnvVar {
	cpu, mem := rp.Spec.RunnerLimits()
	return append(CommonEnv(rp, publicURL),
		corev1.EnvVar{Name: "EAGLE_EXECUTOR", Value: "kubernetes"},
		corev1.EnvVar{Name: "EAGLE_INSTANCE_NAME", Value: rp.Name},
		corev1.EnvVar{Name: "EAGLE_K8S_NAMESPACE", ValueFrom: &corev1.EnvVarSource{
			FieldRef: &corev1.ObjectFieldSelector{APIVersion: "v1", FieldPath: "metadata.namespace"},
		}},
		corev1.EnvVar{Name: "EAGLE_RUNNER_IMAGE", Value: rp.Spec.Runner.Image},
		corev1.EnvVar{Name: "EAGLE_RUNNER_SERVICE_ACCOUNT", Value: RunnerServiceAccountName(rp)},
		corev1.EnvVar{Name: "EAGLE_CALLBACK_BASE_URL", Value: CallbackBaseURL(rp)},
		corev1.EnvVar{Name: "EAGLE_RUNNER_CPU_LIMIT", Value: cpu},
		corev1.EnvVar{Name: "EAGLE_RUNNER_MEMORY_LIMIT", Value: mem},
	)
}

func restrictedContainerSecurityContext() *corev1.SecurityContext {
	return &corev1.SecurityContext{
		AllowPrivilegeEscalation: ptr.To(false),
		RunAsNonRoot:             ptr.To(true),
		Capabilities:             &corev1.Capabilities{Drop: []corev1.Capability{"ALL"}},
		SeccompProfile:           &corev1.SeccompProfile{Type: corev1.SeccompProfileTypeRuntimeDefault},
	}
}

func podSecurityContext() *corev1.PodSecurityContext {
	return &corev1.PodSecurityContext{
		RunAsNonRoot:   ptr.To(true),
		SeccompProfile: &corev1.SeccompProfile{Type: corev1.SeccompProfileTypeRuntimeDefault},
	}
}

func httpProbe(path string, initialDelay, period, failure int32) *corev1.Probe {
	return &corev1.Probe{
		ProbeHandler: corev1.ProbeHandler{HTTPGet: &corev1.HTTPGetAction{
			Path: path, Port: intstr.FromString("http"), Scheme: corev1.URISchemeHTTP,
		}},
		InitialDelaySeconds: initialDelay,
		PeriodSeconds:       period,
		TimeoutSeconds:      5,
		FailureThreshold:    failure,
		SuccessThreshold:    1,
	}
}

// BuildWebDeployment builds the web Deployment with the migrate initContainer.
func BuildWebDeployment(rp *eaglev1alpha1.ReviewPlatform, publicURL string) *appsv1.Deployment {
	labels := Labels(rp, ComponentWeb)
	env := CommonEnv(rp, publicURL)
	return &appsv1.Deployment{
		ObjectMeta: metav1.ObjectMeta{Name: WebName(rp), Namespace: rp.Namespace, Labels: labels},
		Spec: appsv1.DeploymentSpec{
			Replicas: ptr.To(rp.Spec.WebReplicas()),
			Selector: &metav1.LabelSelector{MatchLabels: SelectorLabels(rp, ComponentWeb)},
			Template: corev1.PodTemplateSpec{
				ObjectMeta: metav1.ObjectMeta{Labels: labels},
				Spec: corev1.PodSpec{
					ServiceAccountName:           WebName(rp),
					AutomountServiceAccountToken: ptr.To(false),
					SecurityContext:              podSecurityContext(),
					InitContainers: []corev1.Container{{
						Name:            "migrate",
						Image:           rp.Spec.Image,
						ImagePullPolicy: rp.Spec.ImagePullPolicy,
						Command:         []string{"eagle", "migrate"},
						Env:             env,
						Resources:       rp.Spec.Resources.Web,
						SecurityContext: restrictedContainerSecurityContext(),
					}},
					Containers: []corev1.Container{{
						Name:            "web",
						Image:           rp.Spec.Image,
						ImagePullPolicy: rp.Spec.ImagePullPolicy,
						Ports: []corev1.ContainerPort{{
							Name: "http", ContainerPort: WebPort, Protocol: corev1.ProtocolTCP,
						}},
						Env:             WebEnv(rp, publicURL),
						Resources:       rp.Spec.Resources.Web,
						LivenessProbe:   httpProbe("/healthz", 10, 20, 3),
						ReadinessProbe:  httpProbe("/readyz", 5, 10, 3),
						SecurityContext: restrictedContainerSecurityContext(),
					}},
				},
			},
		},
	}
}

// BuildWorkerDeployment builds the worker Deployment.
func BuildWorkerDeployment(rp *eaglev1alpha1.ReviewPlatform, publicURL string) *appsv1.Deployment {
	labels := Labels(rp, ComponentWorker)
	return &appsv1.Deployment{
		ObjectMeta: metav1.ObjectMeta{Name: WorkerName(rp), Namespace: rp.Namespace, Labels: labels},
		Spec: appsv1.DeploymentSpec{
			Replicas: ptr.To(rp.Spec.WorkerReplicaCount()),
			Selector: &metav1.LabelSelector{MatchLabels: SelectorLabels(rp, ComponentWorker)},
			Template: corev1.PodTemplateSpec{
				ObjectMeta: metav1.ObjectMeta{Labels: labels},
				Spec: corev1.PodSpec{
					ServiceAccountName:           WorkerName(rp),
					AutomountServiceAccountToken: ptr.To(true),
					SecurityContext:              podSecurityContext(),
					Containers: []corev1.Container{{
						Name:            "worker",
						Image:           rp.Spec.Image,
						ImagePullPolicy: rp.Spec.ImagePullPolicy,
						Command:         []string{"eagle", "worker"},
						Env:             WorkerEnv(rp, publicURL),
						Resources:       rp.Spec.Resources.Worker,
						SecurityContext: restrictedContainerSecurityContext(),
					}},
				},
			},
		},
	}
}

// BuildWebService builds the ClusterIP Service in front of the web pods.
func BuildWebService(rp *eaglev1alpha1.ReviewPlatform) *corev1.Service {
	return &corev1.Service{
		ObjectMeta: metav1.ObjectMeta{Name: WebName(rp), Namespace: rp.Namespace, Labels: Labels(rp, ComponentWeb)},
		Spec: corev1.ServiceSpec{
			Type:     corev1.ServiceTypeClusterIP,
			Selector: SelectorLabels(rp, ComponentWeb),
			Ports: []corev1.ServicePort{{
				Name: "http", Port: WebPort, TargetPort: intstr.FromString("http"), Protocol: corev1.ProtocolTCP,
			}},
		},
	}
}

// BuildRoute builds the edge terminated Route for the web Service.
func BuildRoute(rp *eaglev1alpha1.ReviewPlatform) *routev1.Route {
	termination := rp.Spec.Route.TLS.Termination
	if termination == "" {
		termination = eaglev1alpha1.DefaultTLSTermination
	}
	insecure := rp.Spec.Route.TLS.InsecureEdgeTerminationPolicy
	if insecure == "" {
		insecure = string(routev1.InsecureEdgeTerminationPolicyRedirect)
	}
	return &routev1.Route{
		ObjectMeta: metav1.ObjectMeta{Name: WebName(rp), Namespace: rp.Namespace, Labels: Labels(rp, ComponentWeb)},
		Spec: routev1.RouteSpec{
			Host: rp.Spec.Route.Host,
			To:   routev1.RouteTargetReference{Kind: "Service", Name: WebName(rp), Weight: ptr.To[int32](100)},
			Port: &routev1.RoutePort{TargetPort: intstr.FromString("http")},
			TLS: &routev1.TLSConfig{
				Termination:                   routev1.TLSTerminationType(termination),
				InsecureEdgeTerminationPolicy: routev1.InsecureEdgeTerminationPolicyType(insecure),
			},
			WildcardPolicy: routev1.WildcardPolicyNone,
		},
	}
}

// BuildServiceAccounts builds the web, worker and runner ServiceAccounts.
// The runner SA never gets a RoleBinding and does not automount its token.
func BuildServiceAccounts(rp *eaglev1alpha1.ReviewPlatform) []*corev1.ServiceAccount {
	sa := func(name, component string, automount *bool) *corev1.ServiceAccount {
		return &corev1.ServiceAccount{
			ObjectMeta:                   metav1.ObjectMeta{Name: name, Namespace: rp.Namespace, Labels: Labels(rp, component)},
			AutomountServiceAccountToken: automount,
		}
	}
	return []*corev1.ServiceAccount{
		sa(WebName(rp), ComponentWeb, ptr.To(false)),
		sa(WorkerName(rp), ComponentWorker, nil),
		sa(RunnerServiceAccountName(rp), ComponentRunner, ptr.To(false)),
	}
}

// WorkerPolicyRules are the only permissions granted in the namespace: the
// worker launches review Jobs, manages their per-run Secrets and reads logs.
func WorkerPolicyRules() []rbacv1.PolicyRule {
	return []rbacv1.PolicyRule{
		{APIGroups: []string{"batch"}, Resources: []string{"jobs"}, Verbs: []string{"create", "get", "list", "watch", "delete"}},
		{APIGroups: []string{""}, Resources: []string{"secrets"}, Verbs: []string{"create", "get", "delete", "patch"}},
		{APIGroups: []string{""}, Resources: []string{"pods"}, Verbs: []string{"get", "list"}},
		{APIGroups: []string{""}, Resources: []string{"pods/log"}, Verbs: []string{"get"}},
	}
}

// BuildWorkerRole builds the namespaced Role for the worker.
func BuildWorkerRole(rp *eaglev1alpha1.ReviewPlatform) *rbacv1.Role {
	return &rbacv1.Role{
		ObjectMeta: metav1.ObjectMeta{Name: WorkerName(rp), Namespace: rp.Namespace, Labels: Labels(rp, ComponentWorker)},
		Rules:      WorkerPolicyRules(),
	}
}

// BuildWorkerRoleBinding binds the worker Role to the worker ServiceAccount only.
func BuildWorkerRoleBinding(rp *eaglev1alpha1.ReviewPlatform) *rbacv1.RoleBinding {
	return &rbacv1.RoleBinding{
		ObjectMeta: metav1.ObjectMeta{Name: WorkerName(rp), Namespace: rp.Namespace, Labels: Labels(rp, ComponentWorker)},
		RoleRef:    rbacv1.RoleRef{APIGroup: rbacv1.GroupName, Kind: "Role", Name: WorkerName(rp)},
		Subjects: []rbacv1.Subject{{
			Kind: rbacv1.ServiceAccountKind, Name: WorkerName(rp), Namespace: rp.Namespace,
		}},
	}
}

func port(proto corev1.Protocol, p int) networkingv1.NetworkPolicyPort {
	return networkingv1.NetworkPolicyPort{Protocol: ptr.To(proto), Port: ptr.To(intstr.FromInt(p))}
}

// runnerPodSelector matches review Job pods. It uses only the two labels the
// worker is contractually required to set.
func runnerPodSelector(rp *eaglev1alpha1.ReviewPlatform) metav1.LabelSelector {
	return metav1.LabelSelector{MatchLabels: map[string]string{
		LabelComponent: ComponentRunner,
		LabelInstance:  rp.Name,
	}}
}

// BuildRunnerNetworkPolicy denies all ingress to runner pods and allows egress
// only to DNS, the web pods on 8000, and IPv4 addresses outside BlockedCIDRs.
// Ports 5353 are allowed alongside 53 because OpenShift DNS pods listen on
// 5353 and NetworkPolicy is evaluated after the Service DNAT.
func BuildRunnerNetworkPolicy(rp *eaglev1alpha1.ReviewPlatform) *networkingv1.NetworkPolicy {
	return &networkingv1.NetworkPolicy{
		ObjectMeta: metav1.ObjectMeta{Name: RunnerNetworkPolicyName(rp), Namespace: rp.Namespace, Labels: Labels(rp, ComponentRunner)},
		Spec: networkingv1.NetworkPolicySpec{
			PodSelector: runnerPodSelector(rp),
			PolicyTypes: []networkingv1.PolicyType{networkingv1.PolicyTypeIngress, networkingv1.PolicyTypeEgress},
			Ingress:     []networkingv1.NetworkPolicyIngressRule{},
			Egress: []networkingv1.NetworkPolicyEgressRule{
				{Ports: []networkingv1.NetworkPolicyPort{
					port(corev1.ProtocolUDP, 53), port(corev1.ProtocolTCP, 53),
					port(corev1.ProtocolUDP, 5353), port(corev1.ProtocolTCP, 5353),
				}},
				{
					To: []networkingv1.NetworkPolicyPeer{{PodSelector: &metav1.LabelSelector{MatchLabels: map[string]string{
						LabelComponent: ComponentWeb,
						LabelInstance:  rp.Name,
					}}}},
					Ports: []networkingv1.NetworkPolicyPort{port(corev1.ProtocolTCP, WebPort)},
				},
				{To: []networkingv1.NetworkPolicyPeer{{IPBlock: &networkingv1.IPBlock{
					CIDR:   "0.0.0.0/0",
					Except: rp.Spec.RunnerBlockedCIDRs(),
				}}}},
			},
		},
	}
}

// BuildWebNetworkPolicy allows ingress to web pods on 8000 from the OpenShift
// router, from cluster monitoring (for /metrics), and from the same namespace.
func BuildWebNetworkPolicy(rp *eaglev1alpha1.ReviewPlatform) *networkingv1.NetworkPolicy {
	nsPeer := func(k, v string) networkingv1.NetworkPolicyPeer {
		return networkingv1.NetworkPolicyPeer{NamespaceSelector: &metav1.LabelSelector{MatchLabels: map[string]string{k: v}}}
	}
	return &networkingv1.NetworkPolicy{
		ObjectMeta: metav1.ObjectMeta{Name: WebName(rp), Namespace: rp.Namespace, Labels: Labels(rp, ComponentWeb)},
		Spec: networkingv1.NetworkPolicySpec{
			PodSelector: metav1.LabelSelector{MatchLabels: map[string]string{
				LabelComponent: ComponentWeb,
				LabelInstance:  rp.Name,
			}},
			PolicyTypes: []networkingv1.PolicyType{networkingv1.PolicyTypeIngress},
			Ingress: []networkingv1.NetworkPolicyIngressRule{{
				From: []networkingv1.NetworkPolicyPeer{
					// Router namespaces (both label spellings used by OpenShift releases).
					nsPeer("network.openshift.io/policy-group", "ingress"),
					nsPeer("policy-group.network.openshift.io/ingress", ""),
					// Prometheus scraping /metrics.
					nsPeer("network.openshift.io/policy-group", "monitoring"),
					// Any pod in the same namespace (worker, runner callbacks).
					{PodSelector: &metav1.LabelSelector{}},
				},
				Ports: []networkingv1.NetworkPolicyPort{port(corev1.ProtocolTCP, WebPort)},
			}},
		},
	}
}

// BuildPostgresNetworkPolicy allows ingress to Postgres only from the web and
// worker pods of the same instance.
func BuildPostgresNetworkPolicy(rp *eaglev1alpha1.ReviewPlatform) *networkingv1.NetworkPolicy {
	return &networkingv1.NetworkPolicy{
		ObjectMeta: metav1.ObjectMeta{Name: PostgresName(rp), Namespace: rp.Namespace, Labels: Labels(rp, ComponentPostgres)},
		Spec: networkingv1.NetworkPolicySpec{
			PodSelector: metav1.LabelSelector{MatchLabels: SelectorLabels(rp, ComponentPostgres)},
			PolicyTypes: []networkingv1.PolicyType{networkingv1.PolicyTypeIngress},
			Ingress: []networkingv1.NetworkPolicyIngressRule{{
				From: []networkingv1.NetworkPolicyPeer{{PodSelector: &metav1.LabelSelector{
					MatchLabels: map[string]string{LabelInstance: rp.Name},
					MatchExpressions: []metav1.LabelSelectorRequirement{{
						Key: LabelComponent, Operator: metav1.LabelSelectorOpIn,
						Values: []string{ComponentWeb, ComponentWorker},
					}},
				}}},
				Ports: []networkingv1.NetworkPolicyPort{port(corev1.ProtocolTCP, PostgresPort)},
			}},
		},
	}
}

// BuildPostgresService builds the Service for the managed Postgres.
func BuildPostgresService(rp *eaglev1alpha1.ReviewPlatform) *corev1.Service {
	return &corev1.Service{
		ObjectMeta: metav1.ObjectMeta{Name: PostgresName(rp), Namespace: rp.Namespace, Labels: Labels(rp, ComponentPostgres)},
		Spec: corev1.ServiceSpec{
			Type:     corev1.ServiceTypeClusterIP,
			Selector: SelectorLabels(rp, ComponentPostgres),
			Ports: []corev1.ServicePort{{
				Name: "postgres", Port: PostgresPort, TargetPort: intstr.FromString("postgres"), Protocol: corev1.ProtocolTCP,
			}},
		},
	}
}

// PostgresVolumeName is the volumeClaimTemplate name. The resulting PVC is
// data-<name>-db-0 and is retained when the StatefulSet or CR is deleted.
const PostgresVolumeName = "data"

// BuildPostgresStatefulSet builds the single replica managed Postgres.
func BuildPostgresStatefulSet(rp *eaglev1alpha1.ReviewPlatform) *appsv1.StatefulSet {
	labels := Labels(rp, ComponentPostgres)
	creds := DBCredentialsSecretName(rp)
	var storageClass *string
	var resources corev1.ResourceRequirements
	if m := rp.Spec.Database.Managed; m != nil {
		storageClass = m.StorageClassName
		resources = m.Resources
	}
	execProbe := func(cmd []string, delay, period int32) *corev1.Probe {
		return &corev1.Probe{
			ProbeHandler:        corev1.ProbeHandler{Exec: &corev1.ExecAction{Command: cmd}},
			InitialDelaySeconds: delay, PeriodSeconds: period, TimeoutSeconds: 10, FailureThreshold: 3, SuccessThreshold: 1,
		}
	}
	return &appsv1.StatefulSet{
		ObjectMeta: metav1.ObjectMeta{Name: PostgresName(rp), Namespace: rp.Namespace, Labels: labels},
		Spec: appsv1.StatefulSetSpec{
			Replicas:    ptr.To[int32](1),
			ServiceName: PostgresName(rp),
			Selector:    &metav1.LabelSelector{MatchLabels: SelectorLabels(rp, ComponentPostgres)},
			PersistentVolumeClaimRetentionPolicy: &appsv1.StatefulSetPersistentVolumeClaimRetentionPolicy{
				WhenDeleted: appsv1.RetainPersistentVolumeClaimRetentionPolicyType,
				WhenScaled:  appsv1.RetainPersistentVolumeClaimRetentionPolicyType,
			},
			Template: corev1.PodTemplateSpec{
				ObjectMeta: metav1.ObjectMeta{Labels: labels},
				Spec: corev1.PodSpec{
					AutomountServiceAccountToken: ptr.To(false),
					SecurityContext:              podSecurityContext(),
					Containers: []corev1.Container{{
						Name:            "postgres",
						Image:           rp.Spec.PostgresImage(),
						ImagePullPolicy: rp.Spec.ImagePullPolicy,
						Ports: []corev1.ContainerPort{{
							Name: "postgres", ContainerPort: PostgresPort, Protocol: corev1.ProtocolTCP,
						}},
						Env: []corev1.EnvVar{
							secretEnv(KeyPostgresUser, creds, KeyPostgresUser, false),
							secretEnv(KeyPostgresPassword, creds, KeyPostgresPassword, false),
							secretEnv(KeyPostgresDatabase, creds, KeyPostgresDatabase, false),
						},
						Resources:       resources,
						ReadinessProbe:  execProbe([]string{"/usr/libexec/check-container"}, 5, 10),
						LivenessProbe:   execProbe([]string{"/usr/libexec/check-container", "--live"}, 120, 10),
						VolumeMounts:    []corev1.VolumeMount{{Name: PostgresVolumeName, MountPath: "/var/lib/pgsql/data"}},
						SecurityContext: restrictedContainerSecurityContext(),
					}},
				},
			},
			VolumeClaimTemplates: []corev1.PersistentVolumeClaim{{
				ObjectMeta: metav1.ObjectMeta{Name: PostgresVolumeName, Labels: labels},
				Spec: corev1.PersistentVolumeClaimSpec{
					AccessModes:      []corev1.PersistentVolumeAccessMode{corev1.ReadWriteOnce},
					StorageClassName: storageClass,
					Resources: corev1.VolumeResourceRequirements{Requests: corev1.ResourceList{
						corev1.ResourceStorage: rp.Spec.PostgresStorageSize(),
					}},
				},
			}},
		},
	}
}

// PostgresPVCName returns the name of the PVC created from the claim template.
func PostgresPVCName(rp *eaglev1alpha1.ReviewPlatform) string {
	return fmt.Sprintf("%s-%s-0", PostgresVolumeName, PostgresName(rp))
}
