package v1alpha1

import (
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/resource"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

// Condition types reported in ReviewPlatformStatus.Conditions.
const (
	// ConditionReady is True when every component is reconciled, the database is
	// ready and the web and worker Deployments are available.
	ConditionReady = "Ready"
	// ConditionDatabaseReady is True when the managed Postgres StatefulSet has a
	// ready replica, or the external database secret exists with the expected key.
	ConditionDatabaseReady = "DatabaseReady"
	// ConditionAvailable is True when the web and worker Deployments report
	// at least one available replica each (or are intentionally scaled to zero).
	ConditionAvailable = "Available"
)

// Phase values reported in ReviewPlatformStatus.Phase.
const (
	PhasePending     = "Pending"
	PhaseProgressing = "Progressing"
	PhaseReady       = "Ready"
	PhaseFailed      = "Failed"
)

// Defaults applied when the corresponding field is not set.
const (
	DefaultBootstrapAdminUsername = "admin"
	DefaultPostgresImage          = "registry.redhat.io/rhel9/postgresql-16:latest"
	DefaultExternalDatabaseKey    = "url"
	DefaultPasswordSecretKey      = "password"
	DefaultRunnerCPULimit         = "2"
	DefaultRunnerMemoryLimit      = "4Gi"
	DefaultTLSTermination         = "edge"
	DefaultOIDCScopes             = "openid email profile"
	DefaultOIDCRole               = "viewer"
	DefaultClientSecretKey        = "clientSecret"
)

// DefaultStorageSize is the default size of the managed Postgres volume.
var DefaultStorageSize = resource.MustParse("5Gi")

// DefaultBlockedCIDRs are the private IPv4 ranges runner pods may not reach.
var DefaultBlockedCIDRs = []string{"10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"}

// RunnerSpec configures the review Job pods that the worker launches.
type RunnerSpec struct {
	// Image is the agent runner image used for review Jobs. Empty uses the
	// operator's default runner image. The operator only passes it to the
	// worker via EAGLE_RUNNER_IMAGE.
	// +operator-sdk:csv:customresourcedefinitions:type=spec,displayName="Runner image",xDescriptors={"urn:alm:descriptor:com.tectonic.ui:text","urn:alm:descriptor:com.tectonic.ui:advanced"}
	// +optional
	Image string `json:"image,omitempty"`

	// Resources for runner pods. Only limits.cpu and limits.memory are passed
	// to the worker (EAGLE_RUNNER_CPU_LIMIT, EAGLE_RUNNER_MEMORY_LIMIT).
	// Defaults: cpu "2", memory "4Gi".
	// +operator-sdk:csv:customresourcedefinitions:type=spec,displayName="Resources per review",xDescriptors={"urn:alm:descriptor:com.tectonic.ui:resourceRequirements"}
	// +optional
	Resources corev1.ResourceRequirements `json:"resources,omitempty"`

	// BlockedCIDRs are IPv4 ranges runner pods may not reach. All other IPv4
	// egress is allowed so agents can reach GitHub and inference APIs.
	// An empty or missing list means the defaults (RFC 1918 ranges).
	// If a model endpoint (for example an internal MaaS) has a private address,
	// remove its range here or reviews cannot reach it.
	// +operator-sdk:csv:customresourcedefinitions:type=spec,displayName="Blocked network ranges"
	// +kubebuilder:default={"10.0.0.0/8","172.16.0.0/12","192.168.0.0/16"}
	// +optional
	BlockedCIDRs []string `json:"blockedCIDRs,omitempty"`
}

// RouteTLSSpec configures TLS on the Route.
type RouteTLSSpec struct {
	// Termination type. Only edge is supported because the app serves plain HTTP.
	// +kubebuilder:validation:Enum=edge
	// +kubebuilder:default=edge
	// +optional
	Termination string `json:"termination,omitempty"`

	// InsecureEdgeTerminationPolicy for plain HTTP requests.
	// +kubebuilder:validation:Enum=Redirect;None;Allow
	// +kubebuilder:default=Redirect
	// +optional
	InsecureEdgeTerminationPolicy string `json:"insecureEdgeTerminationPolicy,omitempty"`
}

// RouteSpec configures the external OpenShift Route.
type RouteSpec struct {
	// Enabled creates a Route for the web Service.
	// +operator-sdk:csv:customresourcedefinitions:type=spec,displayName="Create a Route",xDescriptors={"urn:alm:descriptor:com.tectonic.ui:booleanSwitch"}
	// +kubebuilder:default=true
	// +optional
	Enabled *bool `json:"enabled,omitempty"`

	// Host is the external host name. When empty OpenShift assigns one.
	// When the Route is disabled, Host is still used to build EAGLE_PUBLIC_URL
	// (for clusters that expose the web Service some other way).
	// Set a fixed host if you plan to enable single sign-on, so the redirect
	// URL registered with the identity provider does not change.
	// +operator-sdk:csv:customresourcedefinitions:type=spec,displayName="Host name",xDescriptors={"urn:alm:descriptor:com.tectonic.ui:text"}
	// +optional
	Host string `json:"host,omitempty"`

	// TLS settings.
	// +operator-sdk:csv:customresourcedefinitions:type=spec,displayName="TLS",xDescriptors={"urn:alm:descriptor:com.tectonic.ui:advanced"}
	// +optional
	TLS RouteTLSSpec `json:"tls,omitempty"`
}

// ManagedDatabaseSpec configures the Postgres instance managed by the operator.
type ManagedDatabaseSpec struct {
	// Enabled deploys a single replica Postgres StatefulSet.
	// +operator-sdk:csv:customresourcedefinitions:type=spec,displayName="Run PostgreSQL in the cluster",xDescriptors={"urn:alm:descriptor:com.tectonic.ui:booleanSwitch"}
	// +kubebuilder:default=true
	// +optional
	Enabled *bool `json:"enabled,omitempty"`

	// Image is the Postgres image. It must accept the RHEL postgresql image
	// environment (POSTGRESQL_USER, POSTGRESQL_PASSWORD, POSTGRESQL_DATABASE).
	// Empty uses the operator's default PostgreSQL image.
	// +operator-sdk:csv:customresourcedefinitions:type=spec,displayName="PostgreSQL image",xDescriptors={"urn:alm:descriptor:com.tectonic.ui:text","urn:alm:descriptor:com.tectonic.ui:advanced"}
	// +optional
	Image string `json:"image,omitempty"`

	// StorageSize of the data volume. Only applied when the StatefulSet is
	// first created (volumeClaimTemplates are immutable).
	// +operator-sdk:csv:customresourcedefinitions:type=spec,displayName="Storage size",xDescriptors={"urn:alm:descriptor:com.tectonic.ui:text"}
	// +kubebuilder:default="5Gi"
	// +optional
	StorageSize *resource.Quantity `json:"storageSize,omitempty"`

	// StorageClassName for the data volume. Empty uses the cluster default.
	// +operator-sdk:csv:customresourcedefinitions:type=spec,displayName="Storage class",xDescriptors={"urn:alm:descriptor:io.kubernetes:StorageClass"}
	// +optional
	StorageClassName *string `json:"storageClassName,omitempty"`

	// Resources for the Postgres container.
	// +operator-sdk:csv:customresourcedefinitions:type=spec,displayName="PostgreSQL resources",xDescriptors={"urn:alm:descriptor:com.tectonic.ui:resourceRequirements","urn:alm:descriptor:com.tectonic.ui:advanced"}
	// +optional
	Resources corev1.ResourceRequirements `json:"resources,omitempty"`
}

// ExternalDatabaseSpec points at a Secret holding a full database URL.
type ExternalDatabaseSpec struct {
	// SecretName is the Secret holding the database URL
	// (postgresql+asyncpg://user:pass@host:5432/db).
	// +operator-sdk:csv:customresourcedefinitions:type=spec,displayName="Database URL Secret",xDescriptors={"urn:alm:descriptor:io.kubernetes:Secret"}
	// +kubebuilder:validation:MinLength=1
	SecretName string `json:"secretName"`

	// Key in the Secret that holds the URL.
	// +operator-sdk:csv:customresourcedefinitions:type=spec,displayName="Key in the Secret",xDescriptors={"urn:alm:descriptor:com.tectonic.ui:text"}
	// +kubebuilder:default=url
	// +optional
	Key string `json:"key,omitempty"`
}

// DatabaseSpec selects either a managed or an external database. Exactly one
// must be in effect.
// +kubebuilder:validation:XValidation:rule="!(has(self.external) && has(self.managed) && (!has(self.managed.enabled) || self.managed.enabled))",message="database.managed and database.external are mutually exclusive: set managed.enabled=false or remove external"
// +kubebuilder:validation:XValidation:rule="has(self.external) || !has(self.managed) || !has(self.managed.enabled) || self.managed.enabled",message="database.external is required when database.managed.enabled is false"
type DatabaseSpec struct {
	// Managed Postgres deployed by the operator. This is the default.
	// +operator-sdk:csv:customresourcedefinitions:type=spec,displayName="Built-in database"
	// +optional
	Managed *ManagedDatabaseSpec `json:"managed,omitempty"`

	// External database. Mutually exclusive with an enabled managed database.
	// Turn off the built-in database when using this.
	// +operator-sdk:csv:customresourcedefinitions:type=spec,displayName="External database",xDescriptors={"urn:alm:descriptor:com.tectonic.ui:advanced"}
	// +optional
	External *ExternalDatabaseSpec `json:"external,omitempty"`
}

// SecretsSpec configures application key material.
type SecretsSpec struct {
	// ExistingSecret holds EAGLE_SECRET_KEY and EAGLE_ENCRYPTION_KEY. When empty,
	// the operator generates <name>-keys once and never modifies it afterwards.
	// Only for restoring or moving an install: EAGLE_ENCRYPTION_KEY decrypts
	// every credential stored in the database.
	// +operator-sdk:csv:customresourcedefinitions:type=spec,displayName="Existing key Secret",xDescriptors={"urn:alm:descriptor:io.kubernetes:Secret","urn:alm:descriptor:com.tectonic.ui:advanced"}
	// +optional
	ExistingSecret string `json:"existingSecret,omitempty"`
}

// SecretKeyRef references a key in a Secret in the same namespace.
type SecretKeyRef struct {
	// Name of the Secret.
	// +operator-sdk:csv:customresourcedefinitions:type=spec,displayName="Secret",xDescriptors={"urn:alm:descriptor:io.kubernetes:Secret"}
	// +kubebuilder:validation:MinLength=1
	Name string `json:"name"`
	// Key in the Secret. Defaults to "password".
	// +operator-sdk:csv:customresourcedefinitions:type=spec,displayName="Key",xDescriptors={"urn:alm:descriptor:com.tectonic.ui:text"}
	// +optional
	Key string `json:"key,omitempty"`
}

// BootstrapAdminSpec configures the admin account created on first start.
type BootstrapAdminSpec struct {
	// Username of the bootstrap admin.
	// +operator-sdk:csv:customresourcedefinitions:type=spec,displayName="Admin username",xDescriptors={"urn:alm:descriptor:com.tectonic.ui:text"}
	// +kubebuilder:default=admin
	// +optional
	Username string `json:"username,omitempty"`

	// PasswordSecretRef holds the bootstrap admin password. When absent the
	// app generates one and prints it to its log once.
	// +operator-sdk:csv:customresourcedefinitions:type=spec,displayName="Admin password Secret"
	// +optional
	PasswordSecretRef *SecretKeyRef `json:"passwordSecretRef,omitempty"`
}

// SSOSpec configures OpenID Connect single sign-on for the web component.
// The redirect URI to register with the IdP is
// <EAGLE_PUBLIC_URL>/api/v1/auth/oidc/callback.
type SSOSpec struct {
	// Issuer URL of the OIDC provider. SSO is enabled only when both issuer
	// and clientId are set.
	// +operator-sdk:csv:customresourcedefinitions:type=spec,displayName="Issuer URL",xDescriptors={"urn:alm:descriptor:com.tectonic.ui:text"}
	// +optional
	Issuer string `json:"issuer,omitempty"`

	// ClientID registered with the provider.
	// +operator-sdk:csv:customresourcedefinitions:type=spec,displayName="Client ID",xDescriptors={"urn:alm:descriptor:com.tectonic.ui:text"}
	// +optional
	ClientID string `json:"clientId,omitempty"`

	// ClientSecretRef holds the client secret. Key defaults to "clientSecret".
	// +operator-sdk:csv:customresourcedefinitions:type=spec,displayName="Client secret"
	// +optional
	ClientSecretRef *SecretKeyRef `json:"clientSecretRef,omitempty"`

	// Scopes requested, space separated.
	// +operator-sdk:csv:customresourcedefinitions:type=spec,displayName="Scopes",xDescriptors={"urn:alm:descriptor:com.tectonic.ui:text","urn:alm:descriptor:com.tectonic.ui:advanced"}
	// +kubebuilder:default="openid email profile"
	// +optional
	Scopes string `json:"scopes,omitempty"`

	// ButtonLabel shown on the login page.
	// +operator-sdk:csv:customresourcedefinitions:type=spec,displayName="Login button label",xDescriptors={"urn:alm:descriptor:com.tectonic.ui:text"}
	// +optional
	ButtonLabel string `json:"buttonLabel,omitempty"`

	// UsernameClaim is the ID token claim used as the username.
	// +operator-sdk:csv:customresourcedefinitions:type=spec,displayName="Username claim",xDescriptors={"urn:alm:descriptor:com.tectonic.ui:text","urn:alm:descriptor:com.tectonic.ui:advanced"}
	// +optional
	UsernameClaim string `json:"usernameClaim,omitempty"`

	// GroupsClaim is the ID token claim holding the user's groups.
	// +operator-sdk:csv:customresourcedefinitions:type=spec,displayName="Groups claim",xDescriptors={"urn:alm:descriptor:com.tectonic.ui:text","urn:alm:descriptor:com.tectonic.ui:advanced"}
	// +optional
	GroupsClaim string `json:"groupsClaim,omitempty"`

	// AdminGroups map to the admin role.
	// +operator-sdk:csv:customresourcedefinitions:type=spec,displayName="Admin groups"
	// +optional
	AdminGroups []string `json:"adminGroups,omitempty"`

	// MemberGroups map to the member role.
	// +operator-sdk:csv:customresourcedefinitions:type=spec,displayName="Member groups"
	// +optional
	MemberGroups []string `json:"memberGroups,omitempty"`

	// ViewerGroups map to the viewer role.
	// +operator-sdk:csv:customresourcedefinitions:type=spec,displayName="Viewer groups"
	// +optional
	ViewerGroups []string `json:"viewerGroups,omitempty"`

	// DefaultRole for users in none of the mapped groups. "none" denies access.
	// +operator-sdk:csv:customresourcedefinitions:type=spec,displayName="Role for other users",xDescriptors={"urn:alm:descriptor:com.tectonic.ui:select:admin","urn:alm:descriptor:com.tectonic.ui:select:member","urn:alm:descriptor:com.tectonic.ui:select:viewer","urn:alm:descriptor:com.tectonic.ui:select:none"}
	// +kubebuilder:validation:Enum=admin;member;viewer;none
	// +kubebuilder:default=viewer
	// +optional
	DefaultRole string `json:"defaultRole,omitempty"`

	// LocalLoginEnabled keeps username/password login available (break glass).
	// +operator-sdk:csv:customresourcedefinitions:type=spec,displayName="Keep local login",xDescriptors={"urn:alm:descriptor:com.tectonic.ui:booleanSwitch"}
	// +kubebuilder:default=true
	// +optional
	LocalLoginEnabled *bool `json:"localLoginEnabled,omitempty"`
}

// ComponentResources holds resource requirements per component.
type ComponentResources struct {
	// Web container resources.
	// +operator-sdk:csv:customresourcedefinitions:type=spec,displayName="Web resources",xDescriptors={"urn:alm:descriptor:com.tectonic.ui:resourceRequirements"}
	// +optional
	Web corev1.ResourceRequirements `json:"web,omitempty"`
	// Worker container resources.
	// +operator-sdk:csv:customresourcedefinitions:type=spec,displayName="Worker resources",xDescriptors={"urn:alm:descriptor:com.tectonic.ui:resourceRequirements"}
	// +optional
	Worker corev1.ResourceRequirements `json:"worker,omitempty"`
}

// ReviewPlatformSpec defines the desired state of ReviewPlatform.
type ReviewPlatformSpec struct {
	// Image is the Eagle application image shared by web and worker. Empty
	// uses the operator's default image for this release.
	// +operator-sdk:csv:customresourcedefinitions:type=spec,displayName="Application image",xDescriptors={"urn:alm:descriptor:com.tectonic.ui:text","urn:alm:descriptor:com.tectonic.ui:advanced"}
	// +optional
	Image string `json:"image,omitempty"`

	// ImagePullPolicy for the application and Postgres containers.
	// +operator-sdk:csv:customresourcedefinitions:type=spec,displayName="Image pull policy",xDescriptors={"urn:alm:descriptor:com.tectonic.ui:imagePullPolicy","urn:alm:descriptor:com.tectonic.ui:advanced"}
	// +kubebuilder:validation:Enum=Always;IfNotPresent;Never
	// +optional
	ImagePullPolicy corev1.PullPolicy `json:"imagePullPolicy,omitempty"`

	// Replicas of the web Deployment.
	// +operator-sdk:csv:customresourcedefinitions:type=spec,displayName="Web replicas",xDescriptors={"urn:alm:descriptor:com.tectonic.ui:podCount"}
	// +kubebuilder:default=1
	// +kubebuilder:validation:Minimum=0
	// +optional
	Replicas *int32 `json:"replicas,omitempty"`

	// WorkerReplicas of the worker Deployment.
	// +operator-sdk:csv:customresourcedefinitions:type=spec,displayName="Worker replicas",xDescriptors={"urn:alm:descriptor:com.tectonic.ui:podCount"}
	// +kubebuilder:default=1
	// +kubebuilder:validation:Minimum=0
	// +optional
	WorkerReplicas *int32 `json:"workerReplicas,omitempty"`

	// Runner configures review Job pods.
	// +operator-sdk:csv:customresourcedefinitions:type=spec,displayName="Review runners"
	// +optional
	Runner RunnerSpec `json:"runner,omitempty"`

	// Route configures external access.
	// +operator-sdk:csv:customresourcedefinitions:type=spec,displayName="External access"
	// +optional
	Route RouteSpec `json:"route,omitempty"`

	// Database selects a managed or external Postgres.
	// +operator-sdk:csv:customresourcedefinitions:type=spec,displayName="Database"
	// +optional
	Database DatabaseSpec `json:"database,omitempty"`

	// Secrets configures application key material.
	// +operator-sdk:csv:customresourcedefinitions:type=spec,displayName="Encryption keys"
	// +optional
	Secrets SecretsSpec `json:"secrets,omitempty"`

	// BootstrapAdmin configures the first admin account.
	// +operator-sdk:csv:customresourcedefinitions:type=spec,displayName="First admin account"
	// +optional
	BootstrapAdmin BootstrapAdminSpec `json:"bootstrapAdmin,omitempty"`

	// SSO configures OpenID Connect login (web only).
	// +operator-sdk:csv:customresourcedefinitions:type=spec,displayName="Single sign-on (OIDC)"
	// +optional
	SSO *SSOSpec `json:"sso,omitempty"`

	// Resources for the web and worker containers.
	// +operator-sdk:csv:customresourcedefinitions:type=spec,displayName="Resources"
	// +optional
	Resources ComponentResources `json:"resources,omitempty"`
}

// ReviewPlatformStatus defines the observed state of ReviewPlatform.
type ReviewPlatformStatus struct {
	// Conditions: Ready, DatabaseReady, Available.
	// +operator-sdk:csv:customresourcedefinitions:type=status,displayName="Conditions",xDescriptors={"urn:alm:descriptor:io.kubernetes.conditions"}
	// +optional
	// +listType=map
	// +listMapKey=type
	Conditions []metav1.Condition `json:"conditions,omitempty"`

	// URL is the external URL of the platform.
	// +operator-sdk:csv:customresourcedefinitions:type=status,displayName="URL",xDescriptors={"urn:alm:descriptor:org.w3:link"}
	// +optional
	URL string `json:"url,omitempty"`

	// ObservedGeneration is the last generation reconciled.
	// +optional
	ObservedGeneration int64 `json:"observedGeneration,omitempty"`

	// Phase is a short summary: Pending, Progressing, Ready or Failed.
	// +operator-sdk:csv:customresourcedefinitions:type=status,displayName="Phase",xDescriptors={"urn:alm:descriptor:io.kubernetes.phase"}
	// +optional
	Phase string `json:"phase,omitempty"`
}

// +kubebuilder:object:root=true
// +kubebuilder:subresource:status
// +kubebuilder:resource:shortName=rp
// +kubebuilder:printcolumn:name="Phase",type=string,JSONPath=`.status.phase`
// +kubebuilder:printcolumn:name="URL",type=string,JSONPath=`.status.url`
// +kubebuilder:printcolumn:name="Age",type=date,JSONPath=`.metadata.creationTimestamp`

// ReviewPlatform is the Schema for the reviewplatforms API.
// +operator-sdk:csv:customresourcedefinitions:displayName="Review Platform",resources={{Deployment,v1,""},{StatefulSet,v1,""},{Service,v1,""},{Secret,v1,""},{Route,v1,""},{NetworkPolicy,v1,""},{ServiceAccount,v1,""},{Role,v1,""},{RoleBinding,v1,""}}
type ReviewPlatform struct {
	metav1.TypeMeta   `json:",inline"`
	metav1.ObjectMeta `json:"metadata,omitempty"`

	Spec   ReviewPlatformSpec   `json:"spec,omitempty"`
	Status ReviewPlatformStatus `json:"status,omitempty"`
}

// +kubebuilder:object:root=true

// ReviewPlatformList contains a list of ReviewPlatform.
type ReviewPlatformList struct {
	metav1.TypeMeta `json:",inline"`
	metav1.ListMeta `json:"metadata,omitempty"`
	Items           []ReviewPlatform `json:"items"`
}

func init() {
	SchemeBuilder.Register(&ReviewPlatform{}, &ReviewPlatformList{})
}
