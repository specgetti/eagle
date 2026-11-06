package controller

import (
	"fmt"

	eaglev1alpha1 "github.com/specgetti/eagle/operator/api/v1alpha1"
)

// Label keys and values shared by every resource the operator creates.
const (
	LabelName      = "app.kubernetes.io/name"
	LabelInstance  = "app.kubernetes.io/instance"
	LabelComponent = "app.kubernetes.io/component"
	LabelManagedBy = "app.kubernetes.io/managed-by"
	LabelPartOf    = "app.kubernetes.io/part-of"

	AppName   = "eagle"
	ManagedBy = "eagle-operator"

	ComponentWeb      = "eagle-web"
	ComponentWorker   = "eagle-worker"
	ComponentRunner   = "eagle-runner"
	ComponentPostgres = "eagle-postgres"

	// AnnotationRetain marks generated Secrets that intentionally carry no
	// ownerReference and survive deletion of the ReviewPlatform.
	AnnotationRetain = "eagle.redhat.com/retain-on-delete"

	WebPort      = 8000
	PostgresPort = 5432

	// Keys in the generated <name>-keys Secret (or spec.secrets.existingSecret).
	KeySecretKey     = "EAGLE_SECRET_KEY"
	KeyEncryptionKey = "EAGLE_ENCRYPTION_KEY"

	// Keys in the generated <name>-db Secret (RHEL postgresql image env names).
	KeyPostgresUser     = "POSTGRESQL_USER"
	KeyPostgresPassword = "POSTGRESQL_PASSWORD"
	KeyPostgresDatabase = "POSTGRESQL_DATABASE"

	// KeyDatabaseURL is the key in the <name>-database-url Secret.
	KeyDatabaseURL = "url"
)

// WebName is the name of the web Deployment, Service, ServiceAccount and Route.
func WebName(rp *eaglev1alpha1.ReviewPlatform) string { return rp.Name + "-web" }

// WorkerName is the name of the worker Deployment, ServiceAccount, Role and RoleBinding.
func WorkerName(rp *eaglev1alpha1.ReviewPlatform) string { return rp.Name + "-worker" }

// RunnerServiceAccountName is the ServiceAccount used by review Job pods.
func RunnerServiceAccountName(rp *eaglev1alpha1.ReviewPlatform) string { return rp.Name + "-runner" }

// RunnerNetworkPolicyName is the NetworkPolicy applied to review Job pods.
func RunnerNetworkPolicyName(rp *eaglev1alpha1.ReviewPlatform) string { return rp.Name + "-runner" }

// PostgresName is the name of the managed Postgres StatefulSet and Service.
func PostgresName(rp *eaglev1alpha1.ReviewPlatform) string { return rp.Name + "-db" }

// KeysSecretName is the Secret holding EAGLE_SECRET_KEY and EAGLE_ENCRYPTION_KEY.
func KeysSecretName(rp *eaglev1alpha1.ReviewPlatform) string {
	if rp.Spec.Secrets.ExistingSecret != "" {
		return rp.Spec.Secrets.ExistingSecret
	}
	return GeneratedKeysSecretName(rp)
}

// GeneratedKeysSecretName is the name used when the operator generates the keys.
func GeneratedKeysSecretName(rp *eaglev1alpha1.ReviewPlatform) string { return rp.Name + "-keys" }

// DBCredentialsSecretName holds the generated managed Postgres credentials.
func DBCredentialsSecretName(rp *eaglev1alpha1.ReviewPlatform) string { return rp.Name + "-db" }

// DatabaseURLSecretName holds EAGLE_DATABASE_URL built from the credentials.
func DatabaseURLSecretName(rp *eaglev1alpha1.ReviewPlatform) string { return rp.Name + "-database-url" }

// CallbackBaseURL is the in-cluster URL runner pods use to reach the web Service.
func CallbackBaseURL(rp *eaglev1alpha1.ReviewPlatform) string {
	return fmt.Sprintf("http://%s.%s.svc:%d", WebName(rp), rp.Namespace, WebPort)
}

// Labels returns the full label set for a component.
func Labels(rp *eaglev1alpha1.ReviewPlatform, component string) map[string]string {
	l := SelectorLabels(rp, component)
	l[LabelManagedBy] = ManagedBy
	l[LabelPartOf] = AppName
	return l
}

// SelectorLabels returns the immutable subset of labels used in selectors.
func SelectorLabels(rp *eaglev1alpha1.ReviewPlatform, component string) map[string]string {
	return map[string]string{
		LabelName:      AppName,
		LabelInstance:  rp.Name,
		LabelComponent: component,
	}
}
