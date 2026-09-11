package main

import (
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"net"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/redis/go-redis/v9"
)

const (
	modeDark    = "dark"
	modeEnabled = "enabled"
	engineOwner = "go"
)

type config struct {
	Mode                  string
	RedisOptions          *redis.Options
	LuaPath               string
	Namespace             string
	Route                 routeIdentity
	RendererAddress       string
	RendererServerName    string
	CAPath                string
	ClientCertificatePath string
	ClientKeyPath         string
	CAPin                 string
	ServerLeafPin         string
	ServerSPKIPin         string
	ExecutorSocket        string
	MetricsAddress        string
	DefaultDelay          string
	LeaseTTL              time.Duration
	ExecutorTimeout       time.Duration
}

func configFromEnvironment() (config, error) {
	mode := env("LIGHTPANDA_B0_SUPERVISOR_MODE", modeDark)
	if mode != modeDark && mode != modeEnabled {
		return config{}, errors.New("LIGHTPANDA_B0_SUPERVISOR_MODE must be dark or enabled")
	}
	result := config{Mode: mode}
	if mode == modeDark {
		return result, nil
	}
	redisOptions, err := redis.ParseURL(requiredEnv("REDIS_URL"))
	if err != nil || redisOptions == nil {
		return config{}, errors.New("REDIS_URL must be a valid standalone Redis URL")
	}
	epoch, err := strconv.ParseInt(requiredEnv("LIGHTPANDA_B0_ROUTING_EPOCH"), 10, 64)
	if err != nil {
		return config{}, errors.New("LIGHTPANDA_B0_ROUTING_EPOCH must be a canonical positive integer")
	}
	if strconv.FormatInt(epoch, 10) != requiredEnv("LIGHTPANDA_B0_ROUTING_EPOCH") {
		return config{}, errors.New("LIGHTPANDA_B0_ROUTING_EPOCH is not canonical")
	}
	host := requiredEnv("LIGHTPANDA_B0_SERVICE_HOST")
	address := net.ParseIP(host)
	if address == nil || address.String() != host || !(address.IsPrivate() && !address.IsLoopback()) {
		return config{}, errors.New("LIGHTPANDA_B0_SERVICE_HOST must be a canonical private IP")
	}
	result = config{
		Mode: mode, RedisOptions: redisOptions,
		LuaPath:         env("LIGHTPANDA_B0_LUA_PATH", "/app/src/lua/lightpanda_b0_queue.lua"),
		Namespace:       requiredEnv("LIGHTPANDA_B0_QUEUE_NAMESPACE"),
		Route:           routeIdentity{ShardID: requiredEnv("LIGHTPANDA_B0_SHARD_ID"), RoutingEpoch: epoch, EngineOwner: engineOwner},
		RendererAddress: net.JoinHostPort(host, "9443"), RendererServerName: host,
		CAPath: requiredEnv("LIGHTPANDA_B0_CA_CERTIFICATE"), ClientCertificatePath: requiredEnv("LIGHTPANDA_B0_CLIENT_CERTIFICATE"), ClientKeyPath: requiredEnv("LIGHTPANDA_B0_CLIENT_PRIVATE_KEY"),
		ExecutorSocket: env("LIGHTPANDA_B0_EXECUTOR_SOCKET", "/run/jobseek-lightpanda-executor/executor.sock"),
		MetricsAddress: env("LIGHTPANDA_B0_METRICS_ADDRESS", "127.0.0.1:9101"), DefaultDelay: env("THROTTLE_DELAY_DEFAULT", "2.0"),
		LeaseTTL: 60 * time.Second, ExecutorTimeout: 15 * time.Second,
	}
	for value, label := range map[string]*string{
		requiredEnv("LIGHTPANDA_B0_CA_SHA256_FILE"):          &result.CAPin,
		requiredEnv("LIGHTPANDA_B0_SERVER_LEAF_SHA256_FILE"): &result.ServerLeafPin,
		requiredEnv("LIGHTPANDA_B0_SERVER_SPKI_SHA256_FILE"): &result.ServerSPKIPin,
	} {
		pin, pinErr := readPin(value)
		if pinErr != nil {
			return config{}, fmt.Errorf("read Lightpanda pin: %w", pinErr)
		}
		*label = pin
	}
	if err := result.validate(); err != nil {
		return config{}, err
	}
	return result, nil
}

func (c config) validate() error {
	if c.Mode != modeEnabled || c.RedisOptions == nil || c.Namespace == "" || c.RendererAddress == "" || c.ExecutorSocket == "" || c.MetricsAddress == "" {
		return errors.New("incomplete enabled B0 supervisor configuration")
	}
	if err := c.Route.validate(); err != nil {
		return err
	}
	if !safeID.MatchString(c.Namespace) || c.LeaseTTL <= 0 || c.LeaseTTL > maxLeaseTTL || c.ExecutorTimeout <= 0 || c.ExecutorTimeout >= c.LeaseTTL/2 {
		return errors.New("invalid enabled B0 supervisor bounds")
	}
	decimal := regexp.MustCompile(`^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$`)
	if !decimal.MatchString(c.DefaultDelay) {
		return errors.New("THROTTLE_DELAY_DEFAULT must be a non-negative decimal")
	}
	for _, path := range []string{c.LuaPath, c.CAPath, c.ClientCertificatePath, c.ClientKeyPath} {
		info, err := os.Stat(path)
		if err != nil || !info.Mode().IsRegular() || info.Size() <= 0 || info.Size() > 2*1024*1024 {
			return fmt.Errorf("required B0 file is missing or unbounded: %s", filepath.Base(path))
		}
	}
	return nil
}

func requiredEnv(name string) string {
	value := os.Getenv(name)
	if value == "" || value != strings.TrimSpace(value) || strings.ContainsAny(value, "\x00\r\n") {
		return ""
	}
	return value
}

func env(name, fallback string) string {
	if value := requiredEnv(name); value != "" {
		return value
	}
	return fallback
}

func readPin(path string) (string, error) {
	contents, err := os.ReadFile(path)
	if err != nil || len(contents) != 65 || contents[64] != '\n' || !hex256.Match(contents[:64]) {
		return "", errors.New("pin file must contain one lowercase SHA-256 and newline")
	}
	return string(contents[:64]), nil
}

func fileSHA256(path string) (string, error) {
	contents, err := os.ReadFile(path)
	if err != nil {
		return "", err
	}
	digest := sha256.Sum256(contents)
	return hex.EncodeToString(digest[:]), nil
}
