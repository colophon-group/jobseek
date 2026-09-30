// Package b0task preserves the Go producer/supervisor B0 task identity codec.
// It is pure: no queue, database, network, credentials or parser execution.
package b0task

import (
	"bytes"
	"crypto/sha1" // Existing Redis record compatibility. //nolint:gosec
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"math/big"
	"net/url"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"unicode"
	"unicode/utf8"
)

const (
	maxInteger     = int64(9_999_999_999_999)
	maxPayload     = 128 * 1024
	queuePolicyKey = "lightpanda-b0-v1"
)

var (
	safeID       = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$`)
	safeDomain   = regexp.MustCompile(`^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*$`)
	safeRevision = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._+\-]{0,63}$`)
	hex256       = regexp.MustCompile(`^[0-9a-f]{64}$`)
)

func set(values ...string) map[string]struct{} {
	out := make(map[string]struct{}, len(values))
	for _, v := range values {
		out[v] = struct{}{}
	}
	return out
}
func contains(values map[string]struct{}, value string) bool { _, ok := values[value]; return ok }

type Route struct {
	ShardID      string
	RoutingEpoch int64
	EngineOwner  string
}

func (r Route) Validate() error {
	if !safeID.MatchString(r.ShardID) || r.RoutingEpoch < 1 || r.RoutingEpoch > maxInteger || r.EngineOwner != "go" {
		return errors.New("invalid Go B0 route")
	}
	return nil
}

type Envelope struct {
	SchemaVersion          string          `json:"schema_version"`
	TaskKind               string          `json:"task_kind"`
	TaskID                 string          `json:"task_id"`
	BoardID                string          `json:"board_id"`
	SourceURL              string          `json:"source_url"`
	PolicyKey              string          `json:"policy_key"`
	Domain                 string          `json:"domain"`
	ShardID                string          `json:"shard_id"`
	RoutingEpoch           int64           `json:"routing_epoch"`
	EngineOwner            string          `json:"engine_owner"`
	ConfigRevision         int64           `json:"config_revision"`
	InitialReadyAtMS       int64           `json:"initial_ready_at_ms"`
	BrowserBackend         string          `json:"browser_backend"`
	RoutingRevision        string          `json:"routing_revision"`
	ScraperType            string          `json:"scraper_type"`
	ScraperStep            int64           `json:"scraper_step"`
	Render                 bool            `json:"render"`
	Wait                   string          `json:"wait"`
	WaitFallback           *string         `json:"wait_fallback"`
	TimeoutMS              int64           `json:"timeout_ms"`
	ParserConfig           json.RawMessage `json:"parser_config"`
	AssignmentDigestSHA256 string          `json:"assignment_digest_sha256"`
}

type Task struct {
	Envelope      Envelope
	Payload       string
	PayloadSHA256 string
	PayloadSHA1   string
	// Local fresh-context ordinal; never part of the queue identity or payload.
	RenderAttempt int
}

func Decode(payload, expectedDigest string, route Route) (Task, error) {
	if len(payload) == 0 || len(payload) > maxPayload || !hex256.MatchString(expectedDigest) {
		return Task{}, errors.New("invalid task payload bounds")
	}
	Digest := sha256.Sum256([]byte(payload))
	if hex.EncodeToString(Digest[:]) != expectedDigest {
		return Task{}, errors.New("task payload digest mismatch")
	}
	decoder := json.NewDecoder(strings.NewReader(payload))
	decoder.DisallowUnknownFields()
	var envelope Envelope
	if err := decoder.Decode(&envelope); err != nil {
		return Task{}, fmt.Errorf("decode task envelope: %w", err)
	}
	if err := decoder.Decode(&struct{}{}); !errors.Is(err, io.EOF) {
		return Task{}, errors.New("task payload has trailing JSON")
	}
	parsed, err := url.Parse(envelope.SourceURL)
	if err != nil || parsed.Scheme != "https" || parsed.Hostname() != envelope.Domain || parsed.User != nil || parsed.Fragment != "" || (parsed.Port() != "" && parsed.Port() != "443") {
		return Task{}, errors.New("invalid task source URL")
	}
	if envelope.SchemaVersion != "lightpanda-b0-task-v1" || envelope.TaskKind != "scrape" ||
		!safeID.MatchString(envelope.TaskID) || !safeID.MatchString(envelope.BoardID) ||
		envelope.PolicyKey != queuePolicyKey || !safeDomain.MatchString(envelope.Domain) ||
		envelope.ShardID != route.ShardID || envelope.RoutingEpoch != route.RoutingEpoch || envelope.EngineOwner != route.EngineOwner ||
		envelope.ConfigRevision < 1 || envelope.ConfigRevision > maxInteger || envelope.InitialReadyAtMS < 0 || envelope.InitialReadyAtMS > maxInteger ||
		envelope.BrowserBackend != "lightpanda" || !safeRevision.MatchString(envelope.RoutingRevision) ||
		(envelope.ScraperType != "json-ld" && envelope.ScraperType != "dom") || envelope.ScraperStep != 0 || !envelope.Render || !ValidNavigationWait(envelope.Wait) ||
		envelope.TimeoutMS < 1 || envelope.TimeoutMS > 120_000 || !hex256.MatchString(envelope.AssignmentDigestSHA256) ||
		len(envelope.ParserConfig) < 2 || envelope.ParserConfig[0] != '{' {
		return Task{}, errors.New("task envelope violates B0 identity")
	}
	canonicalAssignment, err := CanonicalAssignmentJSON(envelope.ParserConfig)
	if err != nil {
		return Task{}, err
	}
	assignmentDigest := sha256.Sum256(canonicalAssignment)
	if hex.EncodeToString(assignmentDigest[:]) != envelope.AssignmentDigestSHA256 {
		return Task{}, errors.New("task assignment digest mismatch")
	}
	if err := ValidateParserConfig(envelope); err != nil {
		return Task{}, err
	}
	legacy := sha1.Sum([]byte(payload)) //nolint:gosec
	return Task{Envelope: envelope, Payload: payload, PayloadSHA256: expectedDigest, PayloadSHA1: hex.EncodeToString(legacy[:])}, nil
}

// DecodeCanonical adds the Python DB executor's exact task serialization proof
// to the existing Go queue codec. Queue decoding retains its prior behavior;
// a database executor requires this stricter boundary before authorization.
func DecodeCanonical(payload, expectedDigest string, route Route) (Task, error) {
	if err := route.Validate(); err != nil {
		return Task{}, err
	}
	task, err := Decode(payload, expectedDigest, route)
	if err != nil {
		return Task{}, err
	}
	for _, r := range task.Envelope.SourceURL {
		if unicode.IsSpace(r) || r < 32 || r == 127 {
			return Task{}, errors.New("task source URL contains whitespace or controls")
		}
	}
	// Reconstruct the complete typed envelope, including required zero/null
	// fields. Canonicalizing the supplied map would accept omitted fields.
	canonical, err := CanonicalJSON(task.Envelope, false)
	if err != nil || !bytes.Equal(canonical, []byte(payload)) {
		return Task{}, errors.New("task payload is not canonical Go-owned B0")
	}
	return task, nil
}

func CanonicalAssignmentJSON(raw json.RawMessage) ([]byte, error) {
	value, err := ParseCanonicalValue(raw)
	if err != nil {
		return nil, errors.New("task parser config is invalid")
	}
	encoded, err := CanonicalJSON(value, true)
	if err != nil {
		return nil, errors.New("task parser config cannot be canonicalized")
	}
	return encoded, nil
}

func ValidateParserConfig(envelope Envelope) error {
	metadata := struct {
		ScraperType  string          `json:"scraper_type"`
		ParserConfig json.RawMessage `json:"scraper_config"`
	}{envelope.ScraperType, envelope.ParserConfig}
	raw, err := json.Marshal(metadata)
	if err != nil {
		return errors.New("task parser config is invalid")
	}
	_, assignment, err := ResolveAssignment(string(raw))
	if err != nil {
		return err
	}
	if assignment.RoutingRevision != envelope.RoutingRevision || assignment.TimeoutMS != envelope.TimeoutMS ||
		assignment.Digest != envelope.AssignmentDigestSHA256 || assignment.ScraperType != envelope.ScraperType ||
		assignment.Wait != envelope.Wait || !SameOptionalString(assignment.WaitFallback, envelope.WaitFallback) {
		return errors.New("task parser config disagrees with assignment")
	}
	return nil
}

type Assignment struct {
	RoutingRevision string
	TimeoutMS       int64
	ScraperType     string
	Wait            string
	WaitFallback    *string
	Digest          string
}

func ResolveAssignment(rawMetadata string) (map[string]any, Assignment, error) {
	metadataValue, err := ParseCanonicalValue([]byte(rawMetadata))
	if err != nil {
		return nil, Assignment{}, errors.New("B0 board metadata is invalid")
	}
	metadata, ok := metadataValue.(map[string]any)
	if !ok {
		return nil, Assignment{}, errors.New("B0 board metadata is not an object")
	}
	ScraperType := "json-ld"
	if rawType, present := metadata["scraper_type"]; present {
		var valid bool
		ScraperType, valid = rawType.(string)
		if !valid || ScraperType == "" {
			return nil, Assignment{}, errors.New("B0 board scraper type is invalid")
		}
	}
	configValue := metadata["scraper_config"]
	if encoded, ok := configValue.(string); ok {
		configValue, err = ParseCanonicalValue([]byte(encoded))
		if err != nil {
			return nil, Assignment{}, errors.New("B0 parser assignment is invalid")
		}
	}
	config, ok := configValue.(map[string]any)
	if !ok || (ScraperType != "json-ld" && ScraperType != "dom") {
		return nil, Assignment{}, errors.New("allowlisted board has no supported parser assignment")
	}
	allowed := set(
		"browser_backend", "routing_revision", "render", "timeout", "wait", "wait_fallback",
		"defaults", "defaults_by_url", "enrich", "ignore_address_region", "ignore_date_posted",
		"ignore_locations", "ignore_valid_through",
	)
	booleanKeys := []string{"ignore_address_region", "ignore_date_posted", "ignore_locations", "ignore_valid_through"}
	if ScraperType == "dom" {
		booleanKeys = []string{"include_header_content", "include_document_title", "include_document_description"}
		allowed = set("browser_backend", "routing_revision", "render", "timeout", "wait", "wait_fallback", "defaults", "defaults_by_url", "enrich", "steps", "scope", "preset", "defaults_by_regex", "gone_url_pattern", "include_header_content", "include_document_title", "include_document_description")
		if err := ValidateDOMConfig(config); err != nil {
			return nil, Assignment{}, err
		}
	}
	if !KeysWithin(config, allowed) {
		return nil, Assignment{}, errors.New("B0 parser assignment has unknown fields")
	}
	for _, key := range []string{"browser_backend", "routing_revision", "render", "timeout", "wait", "wait_fallback"} {
		if _, ok := config[key]; !ok {
			return nil, Assignment{}, errors.New("B0 parser assignment is incomplete")
		}
	}
	revision, revisionOK := config["routing_revision"].(string)
	TimeoutMS, timeoutOK := CanonicalInt(config["timeout"])
	Wait, waitOK := config["wait"].(string)
	var fallback *string
	if config["wait_fallback"] != nil {
		value, valid := config["wait_fallback"].(string)
		if !valid || !ValidNavigationWait(value) {
			return nil, Assignment{}, errors.New("B0 parser fallback is invalid")
		}
		fallback = &value
	}
	if backend, ok := config["browser_backend"].(string); !ok || backend != "lightpanda" || !revisionOK ||
		!safeRevision.MatchString(revision) || config["render"] != true || !waitOK || !ValidNavigationWait(Wait) ||
		(ScraperType == "json-ld" && (Wait != "load" || fallback != nil)) || !timeoutOK || TimeoutMS < 1 || TimeoutMS > 120_000 {
		return nil, Assignment{}, errors.New("B0 parser assignment identity is invalid")
	}
	for _, key := range booleanKeys {
		if value, ok := config[key]; ok {
			if _, valid := value.(bool); !valid {
				return nil, Assignment{}, errors.New("B0 parser assignment boolean is invalid")
			}
		}
	}
	jobFields := set("title", "description", "locations", "employment_type", "job_location_type", "date_posted", "base_salary", "language", "extras", "metadata")
	if defaults, ok := config["defaults"]; ok && defaults != nil {
		values, valid := defaults.(map[string]any)
		if !valid || (ScraperType == "json-ld" && !KeysWithin(values, jobFields)) {
			return nil, Assignment{}, errors.New("B0 parser defaults are invalid")
		}
	}
	if defaultsByURL, ok := config["defaults_by_url"]; ok && defaultsByURL != nil {
		values, valid := defaultsByURL.(map[string]any)
		if !valid {
			return nil, Assignment{}, errors.New("B0 parser URL defaults are invalid")
		}
		for _, raw := range values {
			nested, valid := raw.(map[string]any)
			if !valid || (ScraperType == "json-ld" && !KeysWithin(nested, jobFields)) {
				return nil, Assignment{}, errors.New("B0 parser URL defaults are invalid")
			}
		}
	}
	if enrich, ok := config["enrich"]; ok && enrich != nil {
		values, valid := enrich.([]any)
		if !valid {
			return nil, Assignment{}, errors.New("B0 parser enrich fields are invalid")
		}
		for _, raw := range values {
			field, valid := raw.(string)
			if !valid || !contains(jobFields, field) {
				return nil, Assignment{}, errors.New("B0 parser enrich fields are invalid")
			}
		}
	}
	canonical, err := CanonicalJSON(config, true)
	if err != nil || len(canonical) > 256*1024 {
		return nil, Assignment{}, errors.New("B0 parser assignment exceeds its bound")
	}
	Digest := sha256.Sum256(canonical)
	return config, Assignment{
		RoutingRevision: revision, TimeoutMS: TimeoutMS, ScraperType: ScraperType, Wait: Wait, WaitFallback: fallback, Digest: hex.EncodeToString(Digest[:]),
	}, nil
}

func ValidNavigationWait(value string) bool {
	return value == "commit" || value == "domcontentloaded" || value == "load" || value == "networkidle"
}

func SameOptionalString(left, right *string) bool {
	return (left == nil && right == nil) || (left != nil && right != nil && *left == *right)
}

func ValidateDOMConfig(config map[string]any) error {
	invalid := errors.New("B0 DOM parser configuration is invalid")
	steps, ok := config["steps"].([]any)
	if !ok || len(steps) == 0 {
		return invalid
	}
	for _, raw := range steps {
		if _, ok := raw.(map[string]any); !ok {
			return invalid
		}
	}
	if preset := config["preset"]; preset != nil && preset != "elementor-careers" {
		return invalid
	}
	if raw, present := config["scope"]; present {
		scope, ok := raw.(string)
		if !ok || len([]rune(scope)) > 256 || strings.TrimSpace(scope) == "" || strings.ContainsRune(scope, 0) {
			return invalid
		}
	}
	if raw, present := config["gone_url_pattern"]; present {
		if _, ok := raw.(string); !ok {
			return invalid
		}
	}
	if raw, present := config["defaults_by_regex"]; present {
		rules, ok := raw.([]any)
		if !ok || len(rules) < 1 || len(rules) > 20 {
			return invalid
		}
		for _, rawRule := range rules {
			rule, ok := rawRule.(map[string]any)
			if !ok || len(rule) != 3 || !KeysWithin(rule, set("field", "pattern", "defaults")) {
				return invalid
			}
			if _, ok := rule["field"].(string); !ok {
				return invalid
			}
			if _, ok := rule["pattern"].(string); !ok {
				return invalid
			}
			if _, ok := rule["defaults"].(map[string]any); !ok {
				return invalid
			}
		}
	}
	return nil
}

func CanonicalInt(value any) (int64, bool) {
	number, ok := value.(json.Number)
	if !ok || strings.ContainsAny(string(number), ".eE") {
		return 0, false
	}
	parsed, err := strconv.ParseInt(string(number), 10, 64)
	return parsed, err == nil && strconv.FormatInt(parsed, 10) == string(number)
}

func KeysWithin(values map[string]any, allowed map[string]struct{}) bool {
	for key := range values {
		if !contains(allowed, key) {
			return false
		}
	}
	return true
}

func ParseCanonicalValue(raw []byte) (any, error) {
	if !utf8.Valid(raw) {
		return nil, errors.New("invalid UTF-8 JSON")
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	var value any
	if err := decoder.Decode(&value); err != nil {
		return nil, err
	}
	if err := decoder.Decode(&struct{}{}); !errors.Is(err, io.EOF) {
		return nil, errors.New("trailing JSON")
	}
	return value, nil
}

func CanonicalJSON(value any, ensureASCII bool) ([]byte, error) {
	return AppendCanonicalJSON(nil, value, ensureASCII)
}

func AppendCanonicalJSON(output []byte, value any, ensureASCII bool) ([]byte, error) {
	switch typed := value.(type) {
	case nil:
		return append(output, "null"...), nil
	case bool:
		return strconv.AppendBool(output, typed), nil
	case string:
		return AppendCanonicalString(output, typed, ensureASCII)
	case json.Number:
		number, err := CanonicalNumber(string(typed))
		return append(output, number...), err
	case int:
		return strconv.AppendInt(output, int64(typed), 10), nil
	case int64:
		return strconv.AppendInt(output, typed, 10), nil
	case map[string]string:
		converted := make(map[string]any, len(typed))
		for key, item := range typed {
			converted[key] = item
		}
		return AppendCanonicalJSON(output, converted, ensureASCII)
	case map[string]any:
		keys := make([]string, 0, len(typed))
		for key := range typed {
			keys = append(keys, key)
		}
		sort.Strings(keys)
		output = append(output, '{')
		for index, key := range keys {
			if index != 0 {
				output = append(output, ',')
			}
			var err error
			output, err = AppendCanonicalString(output, key, ensureASCII)
			if err != nil {
				return nil, err
			}
			output = append(output, ':')
			output, err = AppendCanonicalJSON(output, typed[key], ensureASCII)
			if err != nil {
				return nil, err
			}
		}
		return append(output, '}'), nil
	case []any:
		output = append(output, '[')
		for index, item := range typed {
			if index != 0 {
				output = append(output, ',')
			}
			var err error
			output, err = AppendCanonicalJSON(output, item, ensureASCII)
			if err != nil {
				return nil, err
			}
		}
		return append(output, ']'), nil
	default:
		encoded, err := json.Marshal(value)
		if err != nil {
			return nil, err
		}
		parsed, err := ParseCanonicalValue(encoded)
		if err != nil {
			return nil, err
		}
		return AppendCanonicalJSON(output, parsed, ensureASCII)
	}
}

func CanonicalNumber(raw string) (string, error) {
	if !strings.ContainsAny(raw, ".eE") {
		integer := new(big.Int)
		if _, ok := integer.SetString(raw, 10); !ok {
			return "", errors.New("invalid JSON integer")
		}
		return integer.String(), nil
	}
	value, err := strconv.ParseFloat(raw, 64)
	if err != nil || math.IsInf(value, 0) || math.IsNaN(value) {
		return "", errors.New("invalid JSON number")
	}
	result := strconv.FormatFloat(value, 'g', -1, 64)
	if !strings.ContainsAny(result, ".eE") {
		result += ".0"
	}
	return result, nil
}

func AppendCanonicalString(output []byte, value string, ensureASCII bool) ([]byte, error) {
	if !utf8.ValidString(value) {
		return nil, errors.New("invalid UTF-8 string")
	}
	const hexDigits = "0123456789abcdef"
	output = append(output, '"')
	for _, current := range value {
		switch current {
		case '"', '\\':
			output = append(output, '\\', byte(current))
		case '\b':
			output = append(output, '\\', 'b')
		case '\f':
			output = append(output, '\\', 'f')
		case '\n':
			output = append(output, '\\', 'n')
		case '\r':
			output = append(output, '\\', 'r')
		case '\t':
			output = append(output, '\\', 't')
		default:
			if current < 0x20 || (ensureASCII && current > 0x7f) {
				values := []rune{current}
				if current > 0xffff {
					adjusted := current - 0x10000
					values = []rune{0xd800 + adjusted>>10, 0xdc00 + adjusted&0x3ff}
				}
				for _, unit := range values {
					output = append(output, '\\', 'u', hexDigits[unit>>12&0xf], hexDigits[unit>>8&0xf], hexDigits[unit>>4&0xf], hexDigits[unit&0xf])
				}
			} else {
				output = utf8.AppendRune(output, current)
			}
		}
	}
	return append(output, '"'), nil
}
