package executor

import (
	"errors"
	"strconv"
)

type RuntimeConfig struct {
	Shard       string
	Epoch       int64
	databaseURL string
}

var forbiddenEnvironment = []string{
	"REDIS_URL",
	"LIGHTPANDA_B0_CA_CERTIFICATE",
	"LIGHTPANDA_B0_CLIENT_CERTIFICATE",
	"LIGHTPANDA_B0_CLIENT_PRIVATE_KEY",
	"LIGHTPANDA_B0_CA_SHA256_FILE",
	"LIGHTPANDA_B0_SERVER_LEAF_SHA256_FILE",
	"LIGHTPANDA_B0_SERVER_SPKI_SHA256_FILE",
}

// ReadRuntimeConfig checks the installed executor's exact deployment guards.
// Credential values never enter error strings or public route metadata.
func ReadRuntimeConfig(getenv func(string) string) (RuntimeConfig, error) {
	var config RuntimeConfig
	if getenv == nil || getenv("LIGHTPANDA_B0_EXECUTOR_MODE") != "enabled" {
		return config, errors.New("executor mode must be exactly enabled")
	}
	for _, key := range forbiddenEnvironment {
		if getenv(key) != "" {
			return config, errors.New("DB-only executor received a forbidden authority credential")
		}
	}
	if getenv("CRAWLER_DB_POOL_MIN") != "1" || getenv("CRAWLER_DB_POOL_MAX") != "1" {
		return config, errors.New("executor database pool must be exactly one connection")
	}
	if value := getenv("LIGHTPANDA_B0_EXECUTOR_SOCKET"); value != "" && value != SocketPath {
		return config, errors.New("executor socket path must be exact")
	}
	config.Shard = getenv("LIGHTPANDA_B0_SHARD_ID")
	if config.Shard != "lightpanda-b0" {
		return RuntimeConfig{}, errors.New("executor shard identity must be exact")
	}
	text := getenv("LIGHTPANDA_B0_ROUTING_EPOCH")
	valid := len(text) > 0 && text[0] != '0'
	for _, c := range text {
		if c < '0' || c > '9' {
			valid = false
		}
	}
	epoch, err := strconv.ParseInt(text, 10, 64)
	if !valid || err != nil || epoch < 1 || epoch > maxIdentityInteger {
		return RuntimeConfig{}, errors.New("executor routing epoch must be canonical and bounded")
	}
	config.Epoch = epoch
	config.databaseURL = getenv("LOCAL_DATABASE_URL")
	if config.databaseURL == "" {
		return RuntimeConfig{}, errors.New("executor database configuration is missing")
	}
	return config, nil
}
