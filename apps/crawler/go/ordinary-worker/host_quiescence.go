package worker

import (
	"context"
	"os"
	"runtime"
	"strings"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	release "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue/releaseevidence"
	"github.com/jackc/pgx/v5/pgxpool"
)

type HostQuiescenceConfig struct{ containment HostContainmentConfig }

func ReadHostQuiescenceConfig(getenv func(string) string, source string) (HostQuiescenceConfig, error) {
	if getenv == nil || getenv("ORDINARY_GO_WORKER_MODE") != "host-quiesce" {
		return HostQuiescenceConfig{}, errHostPreflight
	}
	c, err := ReadHostContainmentConfig(func(key string) string {
		if key == "ORDINARY_GO_WORKER_MODE" {
			return "host-contain"
		}
		return getenv(key)
	}, source)
	if err != nil {
		return HostQuiescenceConfig{}, errHostPreflight
	}
	return HostQuiescenceConfig{c}, nil
}

// ClearHostDatabaseEnvironment affects only the explicit coordinator process.
// Credentials come from the verified selected generation, not PG*/caller env.
func ClearHostDatabaseEnvironment() error {
	for _, pair := range os.Environ() {
		key, _, _ := strings.Cut(pair, "=")
		if strings.HasPrefix(key, "PG") && os.Unsetenv(key) != nil {
			return errHostPreflight
		}
	}
	return nil
}

func RunHostQuiescence(ctx context.Context, c HostQuiescenceConfig) (*HostContainmentResult, error) {
	if runtime.GOOS != "linux" {
		return nil, errHostPreflight
	}
	return runHostContainmentPhase(ctx, c.containment, hostMutationLock, observeHostPreflight, release.ContainWriters, nil, true, nil)
}

// WithHostQuiescence keeps the shared host lock, contained writer IDs and live
// SQL session across the in-process cold driver. The driver must validate its
// exact release/intent/epoch requests and retain every phase output before the
// next effect. The returned observation is not full migration/runtime admission.
func WithHostQuiescence(ctx context.Context, c HostQuiescenceConfig, cold func(context.Context, *pgxpool.Pool, *queue.HostColdSQL) error) (*HostContainmentResult, error) {
	if cold == nil || runtime.GOOS != "linux" {
		return nil, errHostPreflight
	}
	return runHostContainmentPhase(ctx, c.containment, hostMutationLock, observeHostPreflight, release.ContainWriters, nil, true, cold)
}
