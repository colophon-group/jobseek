package main

import "testing"

func TestExecutorHealthRequiresExactRouteEnvironment(t *testing.T) {
	t.Setenv("LIGHTPANDA_B0_EXECUTOR_SOCKET", "/run/jobseek-lightpanda-executor/executor.sock")
	t.Setenv("LIGHTPANDA_B0_SHARD_ID", "lightpanda-b0")
	t.Setenv("LIGHTPANDA_B0_ROUTING_EPOCH", "18")
	configured, err := executorHealthConfigFromEnvironment()
	if err != nil || configured.Route.RoutingEpoch != 18 || configured.Route.EngineOwner != engineOwner {
		t.Fatalf("valid executor health route rejected: config=%+v err=%v", configured, err)
	}
	for _, test := range []struct {
		name  string
		key   string
		value string
	}{
		{"wrong socket", "LIGHTPANDA_B0_EXECUTOR_SOCKET", "/tmp/executor.sock"},
		{"wrong shard", "LIGHTPANDA_B0_SHARD_ID", "other"},
		{"zero epoch", "LIGHTPANDA_B0_ROUTING_EPOCH", "0"},
		{"noncanonical epoch", "LIGHTPANDA_B0_ROUTING_EPOCH", "018"},
		{"negative epoch", "LIGHTPANDA_B0_ROUTING_EPOCH", "-1"},
	} {
		t.Run(test.name, func(t *testing.T) {
			t.Setenv(test.key, test.value)
			if _, err := executorHealthConfigFromEnvironment(); err == nil {
				t.Fatal("invalid executor health route was accepted")
			}
		})
	}
}
