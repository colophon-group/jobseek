package worker

import (
	"context"
	"os"
	"runtime"
	"strings"

	release "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue/releaseevidence"
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
	return runHostContainmentPhase(ctx, c.containment, hostMutationLock, observeHostPreflight, release.ContainWriters, nil, true)
}
