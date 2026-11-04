package main

import (
	"crypto/tls"
	"flag"
	"os"

	routev1 "github.com/openshift/api/route/v1"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/labels"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/runtime/schema"
	utilruntime "k8s.io/apimachinery/pkg/util/runtime"
	clientgoscheme "k8s.io/client-go/kubernetes/scheme"
	_ "k8s.io/client-go/plugin/pkg/client/auth"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/cache"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/healthz"
	"sigs.k8s.io/controller-runtime/pkg/log/zap"
	"sigs.k8s.io/controller-runtime/pkg/metrics/filters"
	metricsserver "sigs.k8s.io/controller-runtime/pkg/metrics/server"

	eaglev1alpha1 "github.com/hmoghani/eagle/operator/api/v1alpha1"
	"github.com/hmoghani/eagle/operator/internal/controller"
)

var (
	scheme   = runtime.NewScheme()
	setupLog = ctrl.Log.WithName("setup")
)

func init() {
	utilruntime.Must(clientgoscheme.AddToScheme(scheme))
	utilruntime.Must(routev1.AddToScheme(scheme))
	utilruntime.Must(eaglev1alpha1.AddToScheme(scheme))
}

func main() {
	var metricsAddr, probeAddr string
	var enableLeaderElection, secureMetrics, enableHTTP2 bool
	flag.StringVar(&metricsAddr, "metrics-bind-address", "0",
		"The address the metrics endpoint binds to, for example :8443 (secure) or :8080. 0 disables it.")
	flag.StringVar(&probeAddr, "health-probe-bind-address", ":8081", "The address the probe endpoint binds to.")
	flag.BoolVar(&enableLeaderElection, "leader-elect", false,
		"Enable leader election to ensure there is only one active controller manager.")
	flag.BoolVar(&secureMetrics, "metrics-secure", true,
		"Serve metrics over HTTPS with authentication and authorization.")
	flag.BoolVar(&enableHTTP2, "enable-http2", false, "Enable HTTP/2 for the metrics server.")
	opts := zap.Options{Development: false}
	opts.BindFlags(flag.CommandLine)
	flag.Parse()
	ctrl.SetLogger(zap.New(zap.UseFlagOptions(&opts)))

	var tlsOpts []func(*tls.Config)
	if !enableHTTP2 {
		// HTTP/2 rapid reset (CVE-2023-44487, CVE-2023-39325) mitigation.
		tlsOpts = append(tlsOpts, func(c *tls.Config) { c.NextProtos = []string{"http/1.1"} })
	}
	metricsOpts := metricsserver.Options{
		BindAddress:   metricsAddr,
		SecureServing: secureMetrics,
		TLSOpts:       tlsOpts,
	}
	if secureMetrics {
		metricsOpts.FilterProvider = filters.WithAuthenticationAndAuthorization
	}

	// Only cache Secrets the operator labels as its own. Secrets supplied by
	// users are read through the uncached API reader.
	managedSelector := labels.SelectorFromSet(labels.Set{controller.LabelManagedBy: controller.ManagedBy})

	mgr, err := ctrl.NewManager(ctrl.GetConfigOrDie(), ctrl.Options{
		Scheme:                 scheme,
		Metrics:                metricsOpts,
		HealthProbeBindAddress: probeAddr,
		LeaderElection:         enableLeaderElection,
		LeaderElectionID:       "eagle-operator.eagle.redhat.com",
		Cache: cache.Options{ByObject: map[client.Object]cache.ByObject{
			&corev1.Secret{}: {Label: managedSelector},
		}},
	})
	if err != nil {
		setupLog.Error(err, "unable to start manager")
		os.Exit(1)
	}

	routesAvailable := false
	if _, err := mgr.GetRESTMapper().RESTMapping(
		schema.GroupKind{Group: routev1.GroupName, Kind: "Route"}, routev1.GroupVersion.Version); err == nil {
		routesAvailable = true
	} else {
		setupLog.Info("route.openshift.io/v1 not found, Routes will not be managed", "error", err.Error())
	}

	if err := (&controller.ReviewPlatformReconciler{
		Client:          mgr.GetClient(),
		Scheme:          mgr.GetScheme(),
		APIReader:       mgr.GetAPIReader(),
		RoutesAvailable: routesAvailable,
		Images: controller.DefaultImages{
			App:      os.Getenv(controller.EnvAppImage),
			Runner:   os.Getenv(controller.EnvRunnerImage),
			Postgres: os.Getenv(controller.EnvPostgresImage),
		},
	}).SetupWithManager(mgr); err != nil {
		setupLog.Error(err, "unable to create controller", "controller", "ReviewPlatform")
		os.Exit(1)
	}

	if err := mgr.AddHealthzCheck("healthz", healthz.Ping); err != nil {
		setupLog.Error(err, "unable to set up health check")
		os.Exit(1)
	}
	if err := mgr.AddReadyzCheck("readyz", healthz.Ping); err != nil {
		setupLog.Error(err, "unable to set up ready check")
		os.Exit(1)
	}

	setupLog.Info("starting manager", "routesAvailable", routesAvailable,
		"defaultAppImage", os.Getenv(controller.EnvAppImage),
		"defaultRunnerImage", os.Getenv(controller.EnvRunnerImage))
	if err := mgr.Start(ctrl.SetupSignalHandler()); err != nil {
		setupLog.Error(err, "problem running manager")
		os.Exit(1)
	}
}
