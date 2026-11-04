package controller

import (
	"context"
	"crypto/rand"
	"errors"
	"fmt"
	"io"
	"maps"
	"strings"
	"time"

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
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/controller/controllerutil"
	logf "sigs.k8s.io/controller-runtime/pkg/log"

	eaglev1alpha1 "github.com/hmoghani/eagle/operator/api/v1alpha1"
)

// requeueNotReady is used while waiting on things the operator does not watch
// (user supplied Secrets) or that take a while (database start).
const requeueNotReady = 30 * time.Second

// ReviewPlatformReconciler reconciles a ReviewPlatform object.
type ReviewPlatformReconciler struct {
	client.Client
	Scheme *runtime.Scheme
	// APIReader reads directly from the API server. It is used for Secrets the
	// operator does not own (the Secret cache is label filtered) and for the
	// generate-once check so a stale cache can never trigger regeneration.
	APIReader client.Reader
	// RoutesAvailable is false on clusters without route.openshift.io.
	RoutesAvailable bool
	// Rand is the entropy source for generated secrets (crypto/rand if nil).
	Rand io.Reader
	// Images are used when a ReviewPlatform leaves an image empty. They come
	// from the operator's RELATED_IMAGE_* environment, which OLM rewrites for
	// mirrored (disconnected) installs.
	Images DefaultImages
}

// DefaultImages are the operator's built-in images for this release.
type DefaultImages struct {
	App      string
	Runner   string
	Postgres string
}

// Environment variables holding the default images (OLM relatedImages convention).
const (
	EnvAppImage      = "RELATED_IMAGE_EAGLE"
	EnvRunnerImage   = "RELATED_IMAGE_EAGLE_RUNNER"
	EnvPostgresImage = "RELATED_IMAGE_POSTGRESQL"
)

// ApplyTo fills empty image fields of spec in memory. The stored object is not
// changed, so a later operator upgrade moves instances to its new defaults.
func (d DefaultImages) ApplyTo(spec *eaglev1alpha1.ReviewPlatformSpec) {
	if spec.Image == "" {
		spec.Image = d.App
	}
	if spec.Runner.Image == "" {
		spec.Runner.Image = d.Runner
	}
	if d.Postgres != "" && spec.ManagedDatabaseEnabled() {
		if spec.Database.Managed == nil {
			spec.Database.Managed = &eaglev1alpha1.ManagedDatabaseSpec{}
		}
		if spec.Database.Managed.Image == "" {
			spec.Database.Managed.Image = d.Postgres
		}
	}
}

// +kubebuilder:rbac:groups=eagle.redhat.com,resources=reviewplatforms,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups=eagle.redhat.com,resources=reviewplatforms/status,verbs=get;update;patch
// +kubebuilder:rbac:groups=eagle.redhat.com,resources=reviewplatforms/finalizers,verbs=update
// +kubebuilder:rbac:groups=apps,resources=deployments;statefulsets,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups="",resources=services;serviceaccounts;secrets,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups="",resources=events,verbs=create;patch
// +kubebuilder:rbac:groups=rbac.authorization.k8s.io,resources=roles;rolebindings,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups=networking.k8s.io,resources=networkpolicies,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups=route.openshift.io,resources=routes,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups=route.openshift.io,resources=routes/custom-host,verbs=create;update;patch
// The operator must hold every permission it grants to the worker Role
// (Kubernetes RBAC escalation prevention), hence the rules below.
// +kubebuilder:rbac:groups=batch,resources=jobs,verbs=get;list;watch;create;delete
// +kubebuilder:rbac:groups="",resources=pods,verbs=get;list
// +kubebuilder:rbac:groups="",resources=pods/log,verbs=get

// Reconcile drives the cluster toward the state described by a ReviewPlatform.
func (r *ReviewPlatformReconciler) Reconcile(ctx context.Context, req ctrl.Request) (ctrl.Result, error) {
	log := logf.FromContext(ctx)

	rp := &eaglev1alpha1.ReviewPlatform{}
	if err := r.Get(ctx, req.NamespacedName, rp); err != nil {
		return ctrl.Result{}, client.IgnoreNotFound(err)
	}
	if !rp.DeletionTimestamp.IsZero() {
		// Owned objects are garbage collected; retained Secrets and the PVC stay.
		return ctrl.Result{}, nil
	}

	r.Images.ApplyTo(&rp.Spec)
	if err := rp.Spec.Validate(); err != nil {
		log.Info("invalid spec", "error", err.Error())
		return ctrl.Result{}, r.setFailed(ctx, rp, "InvalidSpec", err.Error())
	}

	st := &reconcileState{}
	steps := []struct {
		name string
		fn   func(context.Context, *eaglev1alpha1.ReviewPlatform, *reconcileState) error
	}{
		{"keys", r.reconcileKeysSecret},
		{"rbac", r.reconcileRBAC},
		{"database", r.reconcileDatabase},
		{"web service", r.reconcileWebService},
		{"route", r.reconcileRoute},
		{"deployments", r.reconcileDeployments},
		{"network policies", r.reconcileNetworkPolicies},
	}
	for _, s := range steps {
		if err := s.fn(ctx, rp, st); err != nil {
			var blocked *blockedError
			if errors.As(err, &blocked) {
				log.Info("waiting", "step", s.name, "reason", blocked.reason, "message", blocked.msg)
				if serr := r.setFailed(ctx, rp, blocked.reason, blocked.msg); serr != nil {
					return ctrl.Result{}, serr
				}
				return ctrl.Result{RequeueAfter: requeueNotReady}, nil
			}
			return ctrl.Result{}, fmt.Errorf("reconciling %s: %w", s.name, err)
		}
	}

	if err := r.updateStatus(ctx, rp, st); err != nil {
		return ctrl.Result{}, err
	}
	if !st.databaseReady || !st.available {
		return ctrl.Result{RequeueAfter: requeueNotReady}, nil
	}
	return ctrl.Result{}, nil
}

// reconcileState carries results between reconcile steps.
type reconcileState struct {
	publicURL       string
	databaseReady   bool
	databaseReason  string
	databaseMessage string
	available       bool
	availableMsg    string
}

// blockedError signals a user fixable condition (missing Secret, bad key).
type blockedError struct{ reason, msg string }

func (e *blockedError) Error() string { return e.reason + ": " + e.msg }

func (r *ReviewPlatformReconciler) rand() io.Reader {
	if r.Rand != nil {
		return r.Rand
	}
	return rand.Reader
}

// apply runs CreateOrUpdate on obj, applying mutate and the controller reference.
func (r *ReviewPlatformReconciler) apply(ctx context.Context, rp *eaglev1alpha1.ReviewPlatform, obj client.Object, mutate func()) error {
	_, err := controllerutil.CreateOrUpdate(ctx, r.Client, obj, func() error {
		mutate()
		return controllerutil.SetControllerReference(rp, obj, r.Scheme)
	})
	return err
}

func mergeLabels(obj client.Object, desired map[string]string) {
	l := obj.GetLabels()
	if l == nil {
		l = map[string]string{}
	}
	maps.Copy(l, desired)
	obj.SetLabels(l)
}

func (r *ReviewPlatformReconciler) deleteIfOwned(ctx context.Context, rp *eaglev1alpha1.ReviewPlatform, obj client.Object) error {
	if err := r.Get(ctx, client.ObjectKeyFromObject(obj), obj); err != nil {
		return client.IgnoreNotFound(err)
	}
	if !metav1.IsControlledBy(obj, rp) {
		return nil
	}
	return client.IgnoreNotFound(r.Delete(ctx, obj))
}

// reconcileKeysSecret generates <name>-keys once, or checks the user's Secret.
func (r *ReviewPlatformReconciler) reconcileKeysSecret(ctx context.Context, rp *eaglev1alpha1.ReviewPlatform, _ *reconcileState) error {
	var secret *corev1.Secret
	if name := rp.Spec.Secrets.ExistingSecret; name != "" {
		secret = &corev1.Secret{}
		if err := r.APIReader.Get(ctx, types.NamespacedName{Namespace: rp.Namespace, Name: name}, secret); err != nil {
			if apierrors.IsNotFound(err) {
				return &blockedError{"SecretNotFound", fmt.Sprintf("spec.secrets.existingSecret %q not found", name)}
			}
			return err
		}
	} else {
		desired, err := BuildKeysSecret(rp, r.rand())
		if err != nil {
			return err
		}
		secret, _, err = EnsureSecretOnce(ctx, r.APIReader, r.Client, desired)
		if err != nil {
			return err
		}
	}
	if missing := MissingKeys(secret, KeySecretKey, KeyEncryptionKey); len(missing) > 0 {
		return &blockedError{"SecretInvalid", fmt.Sprintf("secret %q is missing keys %s (the operator never modifies it)",
			secret.Name, strings.Join(missing, ", "))}
	}
	return nil
}

func (r *ReviewPlatformReconciler) reconcileRBAC(ctx context.Context, rp *eaglev1alpha1.ReviewPlatform, _ *reconcileState) error {
	for _, desired := range BuildServiceAccounts(rp) {
		sa := &corev1.ServiceAccount{ObjectMeta: metav1.ObjectMeta{Name: desired.Name, Namespace: desired.Namespace}}
		if err := r.apply(ctx, rp, sa, func() {
			mergeLabels(sa, desired.Labels)
			sa.AutomountServiceAccountToken = desired.AutomountServiceAccountToken
		}); err != nil {
			return err
		}
	}

	desiredRole := BuildWorkerRole(rp)
	role := &rbacv1.Role{ObjectMeta: metav1.ObjectMeta{Name: desiredRole.Name, Namespace: desiredRole.Namespace}}
	if err := r.apply(ctx, rp, role, func() {
		mergeLabels(role, desiredRole.Labels)
		role.Rules = desiredRole.Rules
	}); err != nil {
		return err
	}

	desiredRB := BuildWorkerRoleBinding(rp)
	rb := &rbacv1.RoleBinding{ObjectMeta: metav1.ObjectMeta{Name: desiredRB.Name, Namespace: desiredRB.Namespace}}
	return r.apply(ctx, rp, rb, func() {
		mergeLabels(rb, desiredRB.Labels)
		rb.RoleRef = desiredRB.RoleRef // immutable; always identical for a given name
		rb.Subjects = desiredRB.Subjects
	})
}

func (r *ReviewPlatformReconciler) reconcileDatabase(ctx context.Context, rp *eaglev1alpha1.ReviewPlatform, st *reconcileState) error {
	if !rp.Spec.ManagedDatabaseEnabled() {
		return r.reconcileExternalDatabase(ctx, rp, st)
	}
	return r.reconcileManagedDatabase(ctx, rp, st)
}

func (r *ReviewPlatformReconciler) reconcileExternalDatabase(ctx context.Context, rp *eaglev1alpha1.ReviewPlatform, st *reconcileState) error {
	// Clean up managed database objects left from a previous configuration.
	// The PVC and <name>-db Secret are not owned and are left alone.
	ns := rp.Namespace
	for _, obj := range []client.Object{
		&appsv1.StatefulSet{ObjectMeta: metav1.ObjectMeta{Name: PostgresName(rp), Namespace: ns}},
		&corev1.Service{ObjectMeta: metav1.ObjectMeta{Name: PostgresName(rp), Namespace: ns}},
		&networkingv1.NetworkPolicy{ObjectMeta: metav1.ObjectMeta{Name: PostgresName(rp), Namespace: ns}},
		&corev1.Secret{ObjectMeta: metav1.ObjectMeta{Name: DatabaseURLSecretName(rp), Namespace: ns}},
	} {
		if err := r.deleteIfOwned(ctx, rp, obj); err != nil {
			return err
		}
	}

	name, key := DatabaseURLSource(rp)
	secret := &corev1.Secret{}
	if err := r.APIReader.Get(ctx, types.NamespacedName{Namespace: ns, Name: name}, secret); err != nil {
		if !apierrors.IsNotFound(err) {
			return err
		}
		st.databaseReason, st.databaseMessage = "SecretNotFound", fmt.Sprintf("external database secret %q not found", name)
		return nil
	}
	if len(secret.Data[key]) == 0 {
		st.databaseReason, st.databaseMessage = "SecretInvalid", fmt.Sprintf("external database secret %q has no key %q", name, key)
		return nil
	}
	st.databaseReady, st.databaseReason, st.databaseMessage = true, "ExternalDatabase", "external database secret present"
	return nil
}

func (r *ReviewPlatformReconciler) reconcileManagedDatabase(ctx context.Context, rp *eaglev1alpha1.ReviewPlatform, st *reconcileState) error {
	desiredCreds, err := BuildDBCredentialsSecret(rp, r.rand())
	if err != nil {
		return err
	}
	creds, _, err := EnsureSecretOnce(ctx, r.APIReader, r.Client, desiredCreds)
	if err != nil {
		return err
	}

	desiredURL, err := BuildDatabaseURLSecret(rp, creds)
	if err != nil {
		return &blockedError{"SecretInvalid", err.Error()}
	}
	urlSecret := &corev1.Secret{ObjectMeta: metav1.ObjectMeta{Name: desiredURL.Name, Namespace: desiredURL.Namespace}}
	if err := r.apply(ctx, rp, urlSecret, func() {
		mergeLabels(urlSecret, desiredURL.Labels)
		urlSecret.Type = desiredURL.Type
		urlSecret.Data = desiredURL.Data
	}); err != nil {
		return err
	}

	desiredSvc := BuildPostgresService(rp)
	svc := &corev1.Service{ObjectMeta: metav1.ObjectMeta{Name: desiredSvc.Name, Namespace: desiredSvc.Namespace}}
	if err := r.apply(ctx, rp, svc, func() { mutateService(svc, desiredSvc) }); err != nil {
		return err
	}

	desiredNP := BuildPostgresNetworkPolicy(rp)
	np := &networkingv1.NetworkPolicy{ObjectMeta: metav1.ObjectMeta{Name: desiredNP.Name, Namespace: desiredNP.Namespace}}
	if err := r.apply(ctx, rp, np, func() {
		mergeLabels(np, desiredNP.Labels)
		np.Spec = desiredNP.Spec
	}); err != nil {
		return err
	}

	desiredSTS := BuildPostgresStatefulSet(rp)
	sts := &appsv1.StatefulSet{ObjectMeta: metav1.ObjectMeta{Name: desiredSTS.Name, Namespace: desiredSTS.Namespace}}
	if err := r.apply(ctx, rp, sts, func() { MutateStatefulSet(sts, desiredSTS) }); err != nil {
		return err
	}

	if sts.Status.ReadyReplicas >= 1 {
		st.databaseReady, st.databaseReason, st.databaseMessage = true, "PostgresReady", "managed Postgres is ready"
	} else {
		st.databaseReason, st.databaseMessage = "PostgresStarting", "waiting for the managed Postgres pod to become ready"
	}
	return nil
}

// MutateStatefulSet copies desired into the live StatefulSet. Immutable fields
// (selector, serviceName, volumeClaimTemplates) are only set on creation, so
// changing storageSize or storageClassName later has no effect.
func MutateStatefulSet(live, desired *appsv1.StatefulSet) {
	mergeLabels(live, desired.Labels)
	if live.CreationTimestamp.IsZero() {
		template := live.Spec.Template
		live.Spec = desired.Spec
		live.Spec.Template = template
		setPodTemplate(&live.Spec.Template, desired.Spec.Template)
		return
	}
	live.Spec.Replicas = desired.Spec.Replicas
	setPodTemplate(&live.Spec.Template, desired.Spec.Template)
	live.Spec.PersistentVolumeClaimRetentionPolicy = desired.Spec.PersistentVolumeClaimRetentionPolicy
}

// RestartedAtAnnotation is set on a pod template by `oc rollout restart`.
const RestartedAtAnnotation = "kubectl.kubernetes.io/restartedAt"

// setPodTemplate replaces the live pod template with the desired one, keeping
// the rollout restart annotation. Without this every reconcile would undo
// `oc rollout restart` (the operator would roll the pods straight back).
func setPodTemplate(live *corev1.PodTemplateSpec, desired corev1.PodTemplateSpec) {
	restartedAt, restarted := live.Annotations[RestartedAtAnnotation]
	*live = *desired.DeepCopy()
	if restarted {
		if live.Annotations == nil {
			live.Annotations = map[string]string{}
		}
		live.Annotations[RestartedAtAnnotation] = restartedAt
	}
}

func mutateService(live, desired *corev1.Service) {
	mergeLabels(live, desired.Labels)
	live.Spec.Type = desired.Spec.Type
	live.Spec.Selector = desired.Spec.Selector
	live.Spec.Ports = desired.Spec.Ports // clusterIP is preserved
}

func (r *ReviewPlatformReconciler) reconcileWebService(ctx context.Context, rp *eaglev1alpha1.ReviewPlatform, _ *reconcileState) error {
	desired := BuildWebService(rp)
	svc := &corev1.Service{ObjectMeta: metav1.ObjectMeta{Name: desired.Name, Namespace: desired.Namespace}}
	return r.apply(ctx, rp, svc, func() { mutateService(svc, desired) })
}

func (r *ReviewPlatformReconciler) reconcileRoute(ctx context.Context, rp *eaglev1alpha1.ReviewPlatform, st *reconcileState) error {
	if !r.RoutesAvailable {
		st.publicURL = PublicURL(rp, "")
		return nil
	}
	if !rp.Spec.RouteEnabled() {
		st.publicURL = PublicURL(rp, "")
		return r.deleteIfOwned(ctx, rp, &routev1.Route{ObjectMeta: metav1.ObjectMeta{Name: WebName(rp), Namespace: rp.Namespace}})
	}
	desired := BuildRoute(rp)
	route := &routev1.Route{ObjectMeta: metav1.ObjectMeta{Name: desired.Name, Namespace: desired.Namespace}}
	if err := r.apply(ctx, rp, route, func() { MutateRoute(route, desired) }); err != nil {
		return err
	}
	host := route.Spec.Host
	if host == "" {
		for _, ing := range route.Status.Ingress {
			if ing.Host != "" {
				host = ing.Host
				break
			}
		}
	}
	st.publicURL = PublicURL(rp, host)
	return nil
}

// MutateRoute copies desired into the live Route. An empty desired host keeps
// the host OpenShift assigned, so it does not flap on every reconcile.
func MutateRoute(live, desired *routev1.Route) {
	mergeLabels(live, desired.Labels)
	host := live.Spec.Host
	live.Spec = desired.Spec
	if desired.Spec.Host == "" {
		live.Spec.Host = host
	}
}

func (r *ReviewPlatformReconciler) reconcileDeployments(ctx context.Context, rp *eaglev1alpha1.ReviewPlatform, st *reconcileState) error {
	var msgs []string
	available := true
	for _, desired := range []*appsv1.Deployment{
		BuildWebDeployment(rp, st.publicURL),
		BuildWorkerDeployment(rp, st.publicURL),
	} {
		dep := &appsv1.Deployment{ObjectMeta: metav1.ObjectMeta{Name: desired.Name, Namespace: desired.Namespace}}
		if err := r.apply(ctx, rp, dep, func() {
			mergeLabels(dep, desired.Labels)
			dep.Spec.Replicas = desired.Spec.Replicas
			dep.Spec.Selector = desired.Spec.Selector
			setPodTemplate(&dep.Spec.Template, desired.Spec.Template)
		}); err != nil {
			return err
		}
		if ok, msg := DeploymentAvailable(dep); !ok {
			available = false
			msgs = append(msgs, msg)
		}
	}
	st.available = available
	if available {
		st.availableMsg = "web and worker are available"
	} else {
		st.availableMsg = strings.Join(msgs, "; ")
	}
	return nil
}

// DeploymentAvailable reports whether a Deployment has the replicas it wants
// available. A Deployment scaled to zero counts as available.
func DeploymentAvailable(d *appsv1.Deployment) (bool, string) {
	want := int32(1)
	if d.Spec.Replicas != nil {
		want = *d.Spec.Replicas
	}
	if want == 0 {
		return true, ""
	}
	if d.Status.AvailableReplicas >= 1 {
		return true, ""
	}
	return false, fmt.Sprintf("deployment %s has no available replicas", d.Name)
}

func (r *ReviewPlatformReconciler) reconcileNetworkPolicies(ctx context.Context, rp *eaglev1alpha1.ReviewPlatform, _ *reconcileState) error {
	for _, desired := range []*networkingv1.NetworkPolicy{BuildWebNetworkPolicy(rp), BuildRunnerNetworkPolicy(rp)} {
		np := &networkingv1.NetworkPolicy{ObjectMeta: metav1.ObjectMeta{Name: desired.Name, Namespace: desired.Namespace}}
		if err := r.apply(ctx, rp, np, func() {
			mergeLabels(np, desired.Labels)
			np.Spec = desired.Spec
		}); err != nil {
			return err
		}
	}
	return nil
}

func condition(t string, ok bool, reason, msg string, gen int64) metav1.Condition {
	s := metav1.ConditionFalse
	if ok {
		s = metav1.ConditionTrue
	}
	return metav1.Condition{Type: t, Status: s, Reason: reason, Message: msg, ObservedGeneration: gen}
}

// ComputeStatus fills rp.Status from the reconcile results. It is pure.
func ComputeStatus(rp *eaglev1alpha1.ReviewPlatform, st *reconcileState) {
	gen := rp.Generation
	meta.SetStatusCondition(&rp.Status.Conditions,
		condition(eaglev1alpha1.ConditionDatabaseReady, st.databaseReady, st.databaseReason, st.databaseMessage, gen))
	availReason := "DeploymentsAvailable"
	if !st.available {
		availReason = "DeploymentsUnavailable"
	}
	meta.SetStatusCondition(&rp.Status.Conditions,
		condition(eaglev1alpha1.ConditionAvailable, st.available, availReason, st.availableMsg, gen))

	ready := st.databaseReady && st.available
	if ready {
		meta.SetStatusCondition(&rp.Status.Conditions,
			condition(eaglev1alpha1.ConditionReady, true, "Ready", "all components are ready", gen))
		rp.Status.Phase = eaglev1alpha1.PhaseReady
	} else {
		reason, msg := "Progressing", st.availableMsg
		if !st.databaseReady {
			reason, msg = st.databaseReason, st.databaseMessage
		}
		meta.SetStatusCondition(&rp.Status.Conditions, condition(eaglev1alpha1.ConditionReady, false, reason, msg, gen))
		rp.Status.Phase = eaglev1alpha1.PhaseProgressing
	}
	rp.Status.URL = st.publicURL
	rp.Status.ObservedGeneration = gen
}

func (r *ReviewPlatformReconciler) updateStatus(ctx context.Context, rp *eaglev1alpha1.ReviewPlatform, st *reconcileState) error {
	ComputeStatus(rp, st)
	return r.Status().Update(ctx, rp)
}

func (r *ReviewPlatformReconciler) setFailed(ctx context.Context, rp *eaglev1alpha1.ReviewPlatform, reason, msg string) error {
	gen := rp.Generation
	meta.SetStatusCondition(&rp.Status.Conditions, condition(eaglev1alpha1.ConditionReady, false, reason, msg, gen))
	rp.Status.Phase = eaglev1alpha1.PhaseFailed
	rp.Status.ObservedGeneration = gen
	return r.Status().Update(ctx, rp)
}

// SetupWithManager sets up the controller with the Manager.
func (r *ReviewPlatformReconciler) SetupWithManager(mgr ctrl.Manager) error {
	b := ctrl.NewControllerManagedBy(mgr).
		For(&eaglev1alpha1.ReviewPlatform{}).
		Owns(&appsv1.Deployment{}).
		Owns(&appsv1.StatefulSet{}).
		Owns(&corev1.Service{}).
		Owns(&corev1.ServiceAccount{}).
		Owns(&corev1.Secret{}).
		Owns(&rbacv1.Role{}).
		Owns(&rbacv1.RoleBinding{}).
		Owns(&networkingv1.NetworkPolicy{})
	if r.RoutesAvailable {
		b = b.Owns(&routev1.Route{})
	}
	return b.Named("reviewplatform").Complete(r)
}
