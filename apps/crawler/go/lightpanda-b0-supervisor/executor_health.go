package main

import (
	"context"
	"errors"
	"strconv"
)

// The same attestation used before a real claim is cheap enough for Docker's
// frequent liveness check. It replaces a fresh Python interpreter and asyncio
// startup every five seconds without weakening the route/socket proof.
func executorHealthConfigFromEnvironment() (config, error) {
	socket := requiredEnv("LIGHTPANDA_B0_EXECUTOR_SOCKET")
	shard := requiredEnv("LIGHTPANDA_B0_SHARD_ID")
	epochText := requiredEnv("LIGHTPANDA_B0_ROUTING_EPOCH")
	epoch, err := strconv.ParseInt(epochText, 10, 64)
	if socket != "/run/jobseek-lightpanda-executor/executor.sock" || shard != "lightpanda-b0" ||
		err != nil || epoch < 1 || strconv.FormatInt(epoch, 10) != epochText {
		return config{}, errors.New("invalid executor health route configuration")
	}
	result := config{
		ExecutorSocket: socket,
		Route: routeIdentity{
			ShardID: shard, RoutingEpoch: epoch, EngineOwner: engineOwner,
		},
	}
	if err := result.Route.validate(); err != nil {
		return config{}, err
	}
	return result, nil
}

func checkExecutorHealth(ctx context.Context) error {
	configured, err := executorHealthConfigFromEnvironment()
	if err != nil {
		return err
	}
	return attestPythonExecutorRoute(ctx, configured)
}
