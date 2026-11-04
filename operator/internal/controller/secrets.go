package controller

import (
	"context"
	"encoding/base64"
	"fmt"
	"io"
	"net/url"

	corev1 "k8s.io/api/core/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"
	"sigs.k8s.io/controller-runtime/pkg/client"

	eaglev1alpha1 "github.com/hmoghani/eagle/operator/api/v1alpha1"
)

const (
	alnum             = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
	dbUser            = "eagle"
	dbName            = "eagle"
	dbPasswordLength  = 32
	secretKeyBytes    = 48
	fernetKeyBytes    = 32
	componentSecrets  = "eagle-secrets"
	componentDatabase = ComponentPostgres
)

// FernetKey returns 32 random bytes, URL safe base64 encoded with padding,
// which is the format cryptography.fernet.Fernet expects.
func FernetKey(r io.Reader) (string, error) {
	b := make([]byte, fernetKeyBytes)
	if _, err := io.ReadFull(r, b); err != nil {
		return "", fmt.Errorf("reading random bytes: %w", err)
	}
	return base64.URLEncoding.EncodeToString(b), nil
}

// RandomToken returns n random bytes, URL safe base64 encoded without padding.
func RandomToken(r io.Reader, n int) (string, error) {
	b := make([]byte, n)
	if _, err := io.ReadFull(r, b); err != nil {
		return "", fmt.Errorf("reading random bytes: %w", err)
	}
	return base64.RawURLEncoding.EncodeToString(b), nil
}

// RandomAlnum returns an alphanumeric string of length n with no modulo bias.
func RandomAlnum(r io.Reader, n int) (string, error) {
	out := make([]byte, 0, n)
	buf := make([]byte, 1)
	// 248 is the largest multiple of 62 below 256; rejecting bytes above it
	// keeps the distribution uniform.
	for len(out) < n {
		if _, err := io.ReadFull(r, buf); err != nil {
			return "", fmt.Errorf("reading random bytes: %w", err)
		}
		if buf[0] >= 248 {
			continue
		}
		out = append(out, alnum[int(buf[0])%len(alnum)])
	}
	return string(out), nil
}

func retainedSecretMeta(rp *eaglev1alpha1.ReviewPlatform, name, component string) metav1.ObjectMeta {
	return metav1.ObjectMeta{
		Name:        name,
		Namespace:   rp.Namespace,
		Labels:      Labels(rp, component),
		Annotations: map[string]string{AnnotationRetain: "true"},
	}
}

// BuildKeysSecret builds a new <name>-keys Secret with fresh random keys. It has
// no ownerReference on purpose: losing EAGLE_ENCRYPTION_KEY loses every
// credential stored in the database.
func BuildKeysSecret(rp *eaglev1alpha1.ReviewPlatform, r io.Reader) (*corev1.Secret, error) {
	secretKey, err := RandomToken(r, secretKeyBytes)
	if err != nil {
		return nil, err
	}
	encKey, err := FernetKey(r)
	if err != nil {
		return nil, err
	}
	return &corev1.Secret{
		ObjectMeta: retainedSecretMeta(rp, GeneratedKeysSecretName(rp), componentSecrets),
		Type:       corev1.SecretTypeOpaque,
		Data: map[string][]byte{
			KeySecretKey:     []byte(secretKey),
			KeyEncryptionKey: []byte(encKey),
		},
	}, nil
}

// BuildDBCredentialsSecret builds a new <name>-db Secret with a random password.
// It has no ownerReference so it survives together with the data volume.
func BuildDBCredentialsSecret(rp *eaglev1alpha1.ReviewPlatform, r io.Reader) (*corev1.Secret, error) {
	pw, err := RandomAlnum(r, dbPasswordLength)
	if err != nil {
		return nil, err
	}
	return &corev1.Secret{
		ObjectMeta: retainedSecretMeta(rp, DBCredentialsSecretName(rp), componentDatabase),
		Type:       corev1.SecretTypeOpaque,
		Data: map[string][]byte{
			KeyPostgresUser:     []byte(dbUser),
			KeyPostgresPassword: []byte(pw),
			KeyPostgresDatabase: []byte(dbName),
		},
	}, nil
}

// DatabaseURL builds the asyncpg URL for the managed Postgres Service.
func DatabaseURL(rp *eaglev1alpha1.ReviewPlatform, creds *corev1.Secret) (string, error) {
	user := string(creds.Data[KeyPostgresUser])
	pw := string(creds.Data[KeyPostgresPassword])
	db := string(creds.Data[KeyPostgresDatabase])
	if user == "" || pw == "" || db == "" {
		return "", fmt.Errorf("secret %s must contain %s, %s and %s",
			creds.Name, KeyPostgresUser, KeyPostgresPassword, KeyPostgresDatabase)
	}
	u := url.URL{
		Scheme: "postgresql+asyncpg",
		User:   url.UserPassword(user, pw),
		Host:   fmt.Sprintf("%s.%s.svc:%d", PostgresName(rp), rp.Namespace, PostgresPort),
		Path:   "/" + db,
	}
	return u.String(), nil
}

// BuildDatabaseURLSecret builds the owned Secret holding EAGLE_DATABASE_URL.
// It is derived data, so it is owned by the CR and rebuilt on every reconcile.
func BuildDatabaseURLSecret(rp *eaglev1alpha1.ReviewPlatform, creds *corev1.Secret) (*corev1.Secret, error) {
	u, err := DatabaseURL(rp, creds)
	if err != nil {
		return nil, err
	}
	return &corev1.Secret{
		ObjectMeta: metav1.ObjectMeta{
			Name:      DatabaseURLSecretName(rp),
			Namespace: rp.Namespace,
			Labels:    Labels(rp, componentDatabase),
		},
		Type: corev1.SecretTypeOpaque,
		Data: map[string][]byte{KeyDatabaseURL: []byte(u)},
	}, nil
}

// EnsureSecretOnce creates desired if no Secret with that name exists and
// otherwise returns the existing Secret untouched. It never updates, so a
// generated key can never be regenerated or overwritten. reader should be an
// uncached reader so a stale cache cannot cause a second generation attempt
// (which would fail with AlreadyExists anyway, as Create never overwrites).
func EnsureSecretOnce(ctx context.Context, reader client.Reader, writer client.Writer, desired *corev1.Secret) (*corev1.Secret, bool, error) {
	existing := &corev1.Secret{}
	key := types.NamespacedName{Namespace: desired.Namespace, Name: desired.Name}
	err := reader.Get(ctx, key, existing)
	if err == nil {
		return existing, false, nil
	}
	if !apierrors.IsNotFound(err) {
		return nil, false, err
	}
	if err := writer.Create(ctx, desired); err != nil {
		if apierrors.IsAlreadyExists(err) {
			if err := reader.Get(ctx, key, existing); err != nil {
				return nil, false, err
			}
			return existing, false, nil
		}
		return nil, false, err
	}
	return desired, true, nil
}

// MissingKeys returns the keys absent or empty in s.
func MissingKeys(s *corev1.Secret, keys ...string) []string {
	var missing []string
	for _, k := range keys {
		if len(s.Data[k]) == 0 {
			missing = append(missing, k)
		}
	}
	return missing
}
