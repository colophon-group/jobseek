// Package chromiumservice defines a dormant, source-only Chromium execution
// boundary. It has no listener, process launcher, queue, or production wiring.
package chromiumservice

import (
	"bytes"
	"encoding/json"
	"errors"
	"io"
	"regexp"
	"strings"
)

const (
	BackendChromium = "chromium"
	LaneChromium    = "chromium"

	maxConfigBytes           = 32 * 1024
	maxConcurrency           = 1024
	maxActiveSessionTTLMS    = 24 * 60 * 60 * 1000
	maxShutdownGraceMS       = 60 * 1000
	maxProcessAgeMS          = 7 * 24 * 60 * 60 * 1000
	maxRSSBytes              = 64 * 1024 * 1024 * 1024
	maxTargets               = 1024
	maxPIDs                  = 32768
	maxFileDescriptors       = 1_000_000
	maxSockets               = 65535
	maxOriginOperations      = 100_000
	maxRequests              = 100_000
	maxTransferBytes         = 10 * 1024 * 1024 * 1024 * 1024
	maxRecycleTasks          = 1_000_000
	maxWritableTmpfsBytes    = 16 * 1024 * 1024 * 1024
	maxUnprivilegedID        = 65535
	requiredSeccompProfile   = "runtime/default"
	runtimeContractVersionV1 = "crawler.runtime/v1"
)

var (
	digestPattern   = regexp.MustCompile(`^sha256:[0-9a-f]{64}$`)
	identityPattern = regexp.MustCompile(`^[0-9A-Za-z][0-9A-Za-z._+-]{0,63}$`)
	socketPattern   = regexp.MustCompile(`^/run/jobseek/chromium/[0-9A-Za-z][0-9A-Za-z._-]{0,63}\.sock$`)
)

// Config is an immutable declaration consumed by New. Values are deliberately
// primitive so callers cannot smuggle environment readers, credentials, or
// live process handles through configuration.
type Config struct {
	Backend              string `json:"backend"`
	ServiceLane          string `json:"service_lane"`
	ImageDigest          string `json:"image_digest"`
	BrowserDigest        string `json:"browser_digest"`
	CDPClientVersion     string `json:"cdp_client_version"`
	BrowserVersion       string `json:"browser_version"`
	SocketPath           string `json:"socket_path"`
	EgressPolicyRevision string `json:"egress_policy_revision"`
	MaxConcurrency       uint32 `json:"max_concurrency"`
	ActiveSessionTTLMS   uint64 `json:"active_session_ttl_ms"`
	ShutdownGraceMS      uint64 `json:"shutdown_grace_ms"`
	MaxProcessAgeMS      uint64 `json:"max_process_age_ms"`
	MaxRSSBytes          uint64 `json:"max_rss_bytes"`
	MaxTargets           uint32 `json:"max_targets"`
	MaxPIDs              uint32 `json:"max_pids"`
	MaxFileDescriptors   uint32 `json:"max_file_descriptors"`
	MaxSockets           uint32 `json:"max_sockets"`
	MaxOriginOperations  uint32 `json:"max_origin_operations"`
	MaxRequests          uint32 `json:"max_requests"`
	MaxTransferBytes     uint64 `json:"max_transfer_bytes"`
	RecycleAfterTasks    uint64 `json:"recycle_after_tasks"`
	RunAsUID             uint32 `json:"run_as_uid"`
	RunAsGID             uint32 `json:"run_as_gid"`
	ReadOnlyRoot         bool   `json:"read_only_root"`
	NoNewPrivileges      bool   `json:"no_new_privileges"`
	DropAllCapabilities  bool   `json:"drop_all_capabilities"`
	SeccompProfile       string `json:"seccomp_profile"`
	WritableTmpfsBytes   uint64 `json:"writable_tmpfs_bytes"`
}

// ConfigErrorCode is bounded and safe to expose in diagnostics.
type ConfigErrorCode string

const (
	ConfigInvalidJSON     ConfigErrorCode = "invalid_json"
	ConfigTrailingData    ConfigErrorCode = "trailing_data"
	ConfigInvalidIdentity ConfigErrorCode = "invalid_identity"
	ConfigInvalidDigest   ConfigErrorCode = "invalid_digest"
	ConfigInvalidEndpoint ConfigErrorCode = "invalid_endpoint"
	ConfigInvalidLimit    ConfigErrorCode = "invalid_limit"
	ConfigInvalidSecurity ConfigErrorCode = "invalid_security"
)

// ConfigError never includes decoder input or secret-bearing values.
type ConfigError struct {
	Code ConfigErrorCode
}

func (e *ConfigError) Error() string {
	return "chromium service config: " + string(e.Code)
}

func configError(code ConfigErrorCode) error {
	return &ConfigError{Code: code}
}

// DecodeConfig accepts one bounded JSON object and rejects unknown fields,
// null coercions, and trailing values. It never reads process environment.
func DecodeConfig(reader io.Reader) (Config, error) {
	if reader == nil {
		return Config{}, configError(ConfigInvalidJSON)
	}
	limited := io.LimitReader(reader, maxConfigBytes+1)
	payload, err := io.ReadAll(limited)
	if err != nil || len(payload) == 0 || len(payload) > maxConfigBytes {
		return Config{}, configError(ConfigInvalidJSON)
	}
	shapeDecoder := json.NewDecoder(bytes.NewReader(payload))
	var shape map[string]json.RawMessage
	if err := shapeDecoder.Decode(&shape); err != nil || shape == nil {
		return Config{}, configError(ConfigInvalidJSON)
	}
	var shapeTrailing any
	if err := shapeDecoder.Decode(&shapeTrailing); !errors.Is(err, io.EOF) {
		return Config{}, configError(ConfigTrailingData)
	}
	for _, value := range shape {
		if bytes.Equal(bytes.TrimSpace(value), []byte("null")) {
			return Config{}, configError(ConfigInvalidJSON)
		}
	}
	decoder := json.NewDecoder(bytes.NewReader(payload))
	decoder.DisallowUnknownFields()
	var config Config
	if err := decoder.Decode(&config); err != nil {
		return Config{}, configError(ConfigInvalidJSON)
	}
	var trailing any
	err = decoder.Decode(&trailing)
	if !errors.Is(err, io.EOF) {
		return Config{}, configError(ConfigTrailingData)
	}
	if err := config.Validate(); err != nil {
		return Config{}, err
	}
	return config, nil
}

// Validate enforces the same closed constraints as config.schema.json.
func (config Config) Validate() error {
	if config.Backend != BackendChromium || config.ServiceLane != LaneChromium ||
		!identityPattern.MatchString(config.CDPClientVersion) ||
		!identityPattern.MatchString(config.BrowserVersion) ||
		!identityPattern.MatchString(config.EgressPolicyRevision) {
		return configError(ConfigInvalidIdentity)
	}
	if !digestPattern.MatchString(config.ImageDigest) ||
		!digestPattern.MatchString(config.BrowserDigest) {
		return configError(ConfigInvalidDigest)
	}
	if !socketPattern.MatchString(config.SocketPath) || strings.Contains(config.SocketPath, "..") {
		return configError(ConfigInvalidEndpoint)
	}
	if !bounded(config.MaxConcurrency, maxConcurrency) ||
		!bounded(config.ActiveSessionTTLMS, uint64(maxActiveSessionTTLMS)) ||
		!bounded(config.ShutdownGraceMS, uint64(maxShutdownGraceMS)) ||
		!bounded(config.MaxProcessAgeMS, uint64(maxProcessAgeMS)) ||
		!bounded(config.MaxRSSBytes, uint64(maxRSSBytes)) ||
		!bounded(config.MaxTargets, maxTargets) ||
		!bounded(config.MaxPIDs, maxPIDs) ||
		!bounded(config.MaxFileDescriptors, maxFileDescriptors) ||
		!bounded(config.MaxSockets, maxSockets) ||
		!bounded(config.MaxOriginOperations, maxOriginOperations) ||
		!bounded(config.MaxRequests, maxRequests) ||
		!bounded(config.MaxTransferBytes, uint64(maxTransferBytes)) ||
		!bounded(config.RecycleAfterTasks, uint64(maxRecycleTasks)) ||
		!bounded(config.WritableTmpfsBytes, uint64(maxWritableTmpfsBytes)) {
		return configError(ConfigInvalidLimit)
	}
	if config.RunAsUID == 0 || config.RunAsUID > maxUnprivilegedID ||
		config.RunAsGID == 0 || config.RunAsGID > maxUnprivilegedID ||
		!config.ReadOnlyRoot || !config.NoNewPrivileges ||
		!config.DropAllCapabilities || config.SeccompProfile != requiredSeccompProfile {
		return configError(ConfigInvalidSecurity)
	}
	return nil
}

type unsigned interface {
	~uint32 | ~uint64
}

func bounded[T unsigned](value T, maximum T) bool {
	return value > 0 && value <= maximum
}
