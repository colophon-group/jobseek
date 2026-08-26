package conformance

import (
	"bytes"
	"crypto/sha256"
	"encoding/base64"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/url"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"

	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	"google.golang.org/protobuf/encoding/protojson"
	"google.golang.org/protobuf/proto"
)

const ContractVersion = "crawler.runtime/v1"

var (
	sha256RE        = regexp.MustCompile(`^[0-9a-f]{64}$`)
	redactedRE      = regexp.MustCompile(`^redacted-sha256:[0-9a-f]{64}$`)
	traceparentRE   = regexp.MustCompile(`^[0-9a-f]{2}-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}(?:-[0-9a-f]+)*$`)
	deadlineRE      = regexp.MustCompile(`^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:Z|[+-]\d{2}:\d{2})$`)
	httpMethodRE    = regexp.MustCompile(`^[A-Z]+$`)
	localeRE        = regexp.MustCompile(`^[a-z]{2,3}(?:-[A-Z][a-z]{3})?(?:-(?:[A-Z]{2}|[0-9]{3}))?$`)
	emailRE         = regexp.MustCompile(`(?i)[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}`)
	redactedEmailRE = regexp.MustCompile(`^person-[0-9a-f]{64}@redacted\.invalid$`)
	phoneRE         = regexp.MustCompile(`\+?[0-9][0-9() .-]{7,}[0-9]`)
	redactedPhoneRE = regexp.MustCompile(`^\+999-[0-9a-f]{24}$`)
	sensitiveKeys   = map[string]bool{
		"access-token": true, "access_token": true, "accesstoken": true,
		"api-key": true, "api_key": true, "apikey": true, "authorization": true,
		"auth": true, "client_secret": true, "clientsecret": true, "cookie": true,
		"credential": true, "key": true, "password": true, "passwd": true,
		"proxy-authorization": true, "refresh_token": true, "refreshtoken": true,
		"secret": true, "session": true, "sessionid": true, "set-cookie": true,
		"sig": true, "signature": true, "token": true, "x-api-key": true,
	}
	localeAliases = map[string]bool{"iw": true, "in": true, "ji": true, "jw": true, "mo": true, "sh": true, "en-UK": true}
	sentinelText  = map[string]bool{"-": true, "n/a": true, "na": true, "none": true, "null": true, "tbd": true, "unknown": true}
	hardLimits    = generatedHardLimits()
)

const maxTransferChunks = 64

const maxExtensionBytes = 65_536

var extensionRegistry = map[string]bool{
	"jobseek.runtime.v1/representative-json/monitor-config|1|2":   true,
	"jobseek.runtime.v1/representative-json/scraper-config|1|2":   true,
	"jobseek.runtime.v1/representative-json/runtime-metadata|1|2": true,
	"jobseek.runtime.v1/browser/evaluation-json|1|2":              true,
}

var extensionContexts = map[string]map[string]bool{
	"jobseek.runtime.v1/representative-json/monitor-config":   {"manifest": true},
	"jobseek.runtime.v1/representative-json/scraper-config":   {"manifest": true},
	"jobseek.runtime.v1/representative-json/runtime-metadata": {"job_content": true, "monitor_metadata": true},
	"jobseek.runtime.v1/browser/evaluation-json":              {"browser_evaluation": true},
}

var scraperExtensionFields = map[string]bool{
	"title": true, "description": true, "locations": true, "employment_type": true,
	"job_location_type": true, "date_posted": true, "base_salary": true, "language": true,
	"localizations": true, "skills": true,
}

type Violation struct {
	Code   string
	Detail string
}

func (v *Violation) Error() string { return v.Code + ": " + v.Detail }

func fail(code, detail string) error { return &Violation{Code: code, Detail: detail} }

func Redact(scope, value string) string {
	sum := sha256.Sum256([]byte(scope + "\x00" + value))
	return "redacted-sha256:" + hex.EncodeToString(sum[:])
}

func LoadCase(raw []byte) (*runtimev1.ConformanceCase, error) {
	result := &runtimev1.ConformanceCase{}
	if err := (protojson.UnmarshalOptions{DiscardUnknown: false}).Unmarshal(raw, result); err != nil {
		return nil, err
	}
	return result, nil
}

func LoadReplay(raw []byte) (*runtimev1.ReplayCase, error) {
	result := &runtimev1.ReplayCase{}
	if err := (protojson.UnmarshalOptions{DiscardUnknown: false}).Unmarshal(raw, result); err != nil {
		return nil, err
	}
	return result, nil
}

func text(value, field string, maximum int) error {
	if len(value) == 0 || len([]byte(value)) > maximum {
		return fail("text", fmt.Sprintf("%s must contain 1..%d UTF-8 bytes", field, maximum))
	}
	return nil
}

func validURL(value, field string) error {
	for i := 0; i < len(value); i++ {
		if value[i] <= 0x20 || value[i] >= 0x7f {
			return fail("url", field+" must contain visible ASCII only")
		}
	}
	if strings.ContainsAny(value, "%\\") {
		return fail("url", field+" must not contain percent escapes or backslashes")
	}
	parsed, err := url.Parse(value)
	if err != nil || (parsed.Scheme != "http" && parsed.Scheme != "https") || parsed.Hostname() == "" {
		return fail("url", field+" must be an absolute HTTP(S) URL")
	}
	if parsed.User != nil || strings.Contains(value, "#") || parsed.Scheme != strings.ToLower(parsed.Scheme) || parsed.Hostname() != strings.ToLower(parsed.Hostname()) {
		return fail("url", field+" must use canonical scheme/host and omit credentials/fragments")
	}
	if port := parsed.Port(); port != "" {
		portValue, err := strconv.ParseUint(port, 10, 16)
		if err != nil || portValue == 0 {
			return fail("url", field+" port must be numeric within 1..65535")
		}
		if (parsed.Scheme == "http" && portValue == 80) || (parsed.Scheme == "https" && portValue == 443) {
			return fail("url", field+" must omit the default port")
		}
	}
	host := parsed.Hostname()
	if strings.HasPrefix(host, ".") || strings.HasSuffix(host, ".") || strings.Contains(host, "_") || !regexp.MustCompile(`^[a-z0-9.-]+$`).MatchString(host) || strings.Contains(host, "..") {
		return fail("url", field+" host is not canonical ASCII")
	}
	if parsed.Path == "" || !strings.HasPrefix(parsed.Path, "/") || strings.Contains(parsed.Path, "//") {
		return fail("url", field+" path must be nonempty and contain no empty segment")
	}
	for _, part := range strings.Split(parsed.Path, "/") {
		if part == "." || part == ".." {
			return fail("url", field+" path must not contain dot segments")
		}
	}
	if strings.Contains(value, "?") && parsed.RawQuery == "" {
		return fail("url", field+" must not contain an empty query delimiter")
	}
	if parsed.RawQuery != "" {
		items := strings.Split(parsed.RawQuery, "&")
		pairs := make([]string, 0, len(items))
		seen := map[string]bool{}
		queryKeyRE := regexp.MustCompile(`^[A-Za-z0-9._~-]+$`)
		queryValueRE := regexp.MustCompile(`^[A-Za-z0-9._~:-]*$`)
		for _, item := range items {
			if strings.Count(item, "=") != 1 {
				return fail("url", field+" query entries require exactly one equals sign")
			}
			pieces := strings.SplitN(item, "=", 2)
			if !queryKeyRE.MatchString(pieces[0]) || !queryValueRE.MatchString(pieces[1]) {
				return fail("url", field+" query pair is not canonical")
			}
			if sensitiveName(pieces[0]) && !redactedRE.MatchString(pieces[1]) {
				return fail("redaction", "sensitive query key is not deterministically redacted")
			}
			if seen[item] {
				return fail("url", field+" query pairs must be unique and sorted")
			}
			seen[item] = true
			pairs = append(pairs, item)
		}
		sorted := append([]string{}, pairs...)
		sort.Strings(sorted)
		if !slicesEqual(pairs, sorted) {
			return fail("url", field+" query pairs must be unique and sorted")
		}
	}
	return nil
}

func slicesEqual(left, right []string) bool {
	if len(left) != len(right) {
		return false
	}
	for i := range left {
		if left[i] != right[i] {
			return false
		}
	}
	return true
}

func sensitiveName(value string) bool {
	name := strings.ToLower(strings.TrimSpace(value))
	if sensitiveKeys[name] {
		return true
	}
	for _, suffix := range []string{"_token", "-token", "_secret", "-secret", "_password", "-password"} {
		if strings.HasSuffix(name, suffix) {
			return true
		}
	}
	return false
}

func canonicalLocale(value string) bool {
	primary := strings.SplitN(value, "-", 2)[0]
	return localeRE.MatchString(value) && !localeAliases[value] && !localeAliases[primary]
}

func validateTraceContext(traceparent *string, tracestate *string) error {
	if traceparent == nil {
		if tracestate != nil {
			return fail("trace", "tracestate requires traceparent")
		}
		return nil
	}
	value := *traceparent
	if !traceparentRE.MatchString(value) {
		return fail("trace", "invalid W3C traceparent")
	}
	parts := strings.Split(value, "-")
	if len(parts) < 4 || parts[0] == "ff" || parts[1] == strings.Repeat("0", 32) || parts[2] == strings.Repeat("0", 16) {
		return fail("trace", "traceparent version and IDs are invalid")
	}
	if parts[0] == "00" && len(parts) != 4 {
		return fail("trace", "traceparent v00 cannot have extensions")
	}
	if tracestate == nil {
		return nil
	}
	state := *tracestate
	if state == "" || len(state) > 512 || strings.Contains(state, " ") {
		return fail("trace", "invalid tracestate whitespace/size")
	}
	members := strings.Split(state, ",")
	if len(members) > 32 {
		return fail("trace", "too many tracestate members")
	}
	keys := map[string]bool{}
	simpleKey := regexp.MustCompile(`^[a-z0-9][_0-9a-z*/-]{0,255}$`)
	multiKey := regexp.MustCompile(`^[a-z0-9][_0-9a-z*/-]{0,240}@[a-z0-9][a-z0-9*/_-]{0,13}$`)
	valueRE := regexp.MustCompile(`^[\x21-\x2B\x2D-\x3C\x3E-\x7E]+$`)
	for _, member := range members {
		if strings.Count(member, "=") != 1 {
			return fail("trace", "invalid tracestate member")
		}
		pieces := strings.SplitN(member, "=", 2)
		if (!simpleKey.MatchString(pieces[0]) && !multiKey.MatchString(pieces[0])) || keys[pieces[0]] {
			return fail("trace", "invalid or duplicate tracestate key")
		}
		keys[pieces[0]] = true
		if len(pieces[1]) > 256 || !valueRE.MatchString(pieces[1]) {
			return fail("trace", "invalid tracestate value")
		}
	}
	return nil
}

func validateRedactedBody(body []byte, field string, depth int) error {
	if len(body) == 0 || depth > 2 || !json.Valid(body) {
		textValue := string(body)
		for _, email := range emailRE.FindAllString(textValue, -1) {
			if !redactedEmailRE.MatchString(email) {
				return fail("redaction", field+" contains an unredacted email")
			}
		}
		for _, phone := range phoneRE.FindAllString(textValue, -1) {
			if !redactedPhoneRE.MatchString(phone) {
				return fail("redaction", field+" contains an unredacted phone")
			}
		}
		lower := strings.ToLower(textValue)
		if strings.Contains(lower, "bearer ") && !strings.Contains(lower, "bearer redacted-sha256:") {
			return fail("redaction", field+" contains an unredacted bearer credential")
		}
		if strings.Contains(textValue, "-----BEGIN ") && strings.Contains(textValue, "PRIVATE KEY-----") {
			return fail("redaction", field+" contains private key material")
		}
		return nil
	}
	var value any
	if err := json.Unmarshal(body, &value); err != nil {
		return nil
	}
	var walk func(any, string) error
	walk = func(current any, path string) error {
		switch typed := current.(type) {
		case map[string]any:
			for key, child := range typed {
				if sensitiveName(key) {
					textValue, ok := child.(string)
					if !ok || !redactedRE.MatchString(textValue) {
						return fail("redaction", path+"."+key+" is not deterministically redacted")
					}
				}
				if err := walk(child, path+"."+key); err != nil {
					return err
				}
			}
		case []any:
			for index, child := range typed {
				if err := walk(child, fmt.Sprintf("%s[%d]", path, index)); err != nil {
					return err
				}
			}
		case string:
			if err := validateRedactedBody([]byte(typed), path, depth+1); err != nil {
				return err
			}
			if len(typed) >= 16 && len(typed)%4 == 0 {
				if decoded, err := base64.StdEncoding.DecodeString(typed); err == nil {
					if err := validateRedactedBody(decoded, path, depth+1); err != nil {
						return err
					}
				}
			}
		}
		return nil
	}
	return walk(value, field)
}

func hash(value, field string) error {
	if !sha256RE.MatchString(value) {
		return fail("hash", field+" must be lowercase sha256 hex")
	}
	return nil
}

func contract(value string) error {
	if value != ContractVersion {
		return fail("version", fmt.Sprintf("expected %s, got %q", ContractVersion, value))
	}
	return nil
}

func FencingDigest(context *runtimev1.FencingContext) []byte {
	values := []string{
		context.GetShardId(),
		fmt.Sprint(context.GetRoutingEpoch()),
		fmt.Sprint(int32(context.GetEngineOwner())),
		context.GetClaimToken(),
		context.GetLeaseId(),
		context.GetConfigRevision(),
	}
	raw := append([]byte("crawler.runtime/v1/fence\x00"), []byte(strings.Join(values, "\x00"))...)
	sum := sha256.Sum256(raw)
	return sum[:]
}

func ValidateFencing(context *runtimev1.FencingContext, configRevision string) error {
	if context == nil {
		return fail("fence", "fencing context is required")
	}
	for field, value := range map[string]string{
		"shard_id": context.GetShardId(), "claim_token": context.GetClaimToken(),
		"lease_id": context.GetLeaseId(), "config_revision": context.GetConfigRevision(),
	} {
		if strings.ContainsRune(value, '\x00') {
			return fail("fence", "fencing."+field+" must not contain NUL")
		}
		if err := text(value, "fencing."+field, 512); err != nil {
			return err
		}
	}
	if context.GetRoutingEpoch() == 0 {
		return fail("fence", "routing_epoch must be positive")
	}
	if context.GetEngineOwner() == 0 || runtimev1.EngineOwner_name[int32(context.GetEngineOwner())] == "" {
		return fail("enum", "fencing engine owner is unspecified or unknown")
	}
	if context.GetConfigRevision() != configRevision {
		return fail("fence", "fencing config revision differs from manifest")
	}
	if len(context.GetFenceDigest()) != sha256.Size {
		return fail("fence", "fencing digest must contain exactly 32 bytes")
	}
	if !bytes.Equal(context.GetFenceDigest(), FencingDigest(context)) {
		return fail("fence", "fencing digest disagrees with canonical typed context")
	}
	return nil
}

func headers(values []*runtimev1.Header, field string) error {
	if len(values) > 256 {
		return fail("limit", field+" exceeds 256 headers")
	}
	seen := map[string]bool{}
	for _, header := range values {
		name := strings.ToLower(strings.TrimSpace(header.GetName()))
		if err := text(name, field+".name", 256); err != nil {
			return err
		}
		if name != header.GetName() {
			return fail("header", field+".name must be canonical lowercase without whitespace")
		}
		if seen[name] {
			return fail("duplicate", "duplicate header "+name)
		}
		seen[name] = true
		if len([]byte(header.GetValue())) > 8_192 {
			return fail("limit", field+"."+name+" exceeds 8192 UTF-8 bytes")
		}
		if strings.ContainsAny(header.GetValue(), "\r\n") {
			return fail("header", name+" contains a line break")
		}
		if sensitiveName(name) && (!header.GetRedacted() || !redactedRE.MatchString(header.GetValue())) {
			return fail("redaction", "sensitive header "+name+" is not deterministically redacted")
		}
	}
	return nil
}

func RequestFingerprint(request *runtimev1.CapturedRequest) string {
	canonical := proto.Clone(request).(*runtimev1.CapturedRequest)
	sort.Slice(canonical.Headers, func(i, j int) bool { return canonical.Headers[i].GetName() < canonical.Headers[j].GetName() })
	raw := append([]byte("crawler.runtime/v1/request\x00"), deterministicBytes(canonical)...)
	sum := sha256.Sum256(raw)
	return hex.EncodeToString(sum[:])
}

func limitValues(l *runtimev1.Limits) []struct {
	name           string
	value, ceiling uint64
} {
	return []struct {
		name           string
		value, ceiling uint64
	}{
		{"max_frame_bytes", l.GetMaxFrameBytes(), hardLimits.GetMaxFrameBytes()},
		{"max_inline_body_bytes", l.GetMaxInlineBodyBytes(), hardLimits.GetMaxInlineBodyBytes()},
		{"max_artifact_chunk_bytes", l.GetMaxArtifactChunkBytes(), hardLimits.GetMaxArtifactChunkBytes()},
		{"max_monitor_batches", uint64(l.GetMaxMonitorBatches()), uint64(hardLimits.GetMaxMonitorBatches())},
		{"max_output_items", l.GetMaxOutputItems(), hardLimits.GetMaxOutputItems()},
		{"max_in_flight_frames", uint64(l.GetMaxInFlightFrames()), uint64(hardLimits.GetMaxInFlightFrames())},
		{"max_active_duration_ms", l.GetMaxActiveDurationMs(), hardLimits.GetMaxActiveDurationMs()},
		{"max_browser_actions", uint64(l.GetMaxBrowserActions()), uint64(hardLimits.GetMaxBrowserActions())},
		{"max_browser_captures", uint64(l.GetMaxBrowserCaptures()), uint64(hardLimits.GetMaxBrowserCaptures())},
		{"max_browser_evaluations", uint64(l.GetMaxBrowserEvaluations()), uint64(hardLimits.GetMaxBrowserEvaluations())},
		{"max_http_transfer_bytes", l.GetMaxHttpTransferBytes(), hardLimits.GetMaxHttpTransferBytes()},
		{"max_browser_transfer_bytes", l.GetMaxBrowserTransferBytes(), hardLimits.GetMaxBrowserTransferBytes()},
		{"max_execution_frames", uint64(l.GetMaxExecutionFrames()), uint64(hardLimits.GetMaxExecutionFrames())},
		{"max_artifact_count", uint64(l.GetMaxArtifactCount()), uint64(hardLimits.GetMaxArtifactCount())},
		{"max_artifact_total_bytes", l.GetMaxArtifactTotalBytes(), hardLimits.GetMaxArtifactTotalBytes()},
	}
}

func ValidateLimits(l, requested *runtimev1.Limits) error {
	if l == nil {
		return fail("limit", "limits are required")
	}
	requestedMap := map[string]uint64{}
	if requested != nil {
		for _, item := range limitValues(requested) {
			requestedMap[item.name] = item.value
		}
	}
	for _, item := range limitValues(l) {
		if item.value == 0 || item.value > item.ceiling {
			return fail("limit", fmt.Sprintf("%s must be within 1..%d", item.name, item.ceiling))
		}
		if requested != nil && item.value > requestedMap[item.name] {
			return fail("negotiation", "accepted "+item.name+" exceeds request")
		}
	}
	return nil
}

func ValidateArtifact(a *runtimev1.ArtifactHandle, limits *runtimev1.Limits) error {
	if a == nil {
		return fail("artifact", "artifact is required")
	}
	if err := text(a.GetHandle(), "artifact.handle", 512); err != nil {
		return err
	}
	if strings.ContainsAny(a.GetHandle(), `/\`) || strings.HasPrefix(a.GetHandle(), ".") {
		return fail("artifact", "artifact handle must be opaque")
	}
	if err := text(a.GetMediaType(), "artifact.media_type", 256); err != nil {
		return err
	}
	if a.GetSizeBytes() > limits.GetMaxArtifactChunkBytes() {
		return fail("artifact_limit", "artifact exceeds max_artifact_chunk_bytes")
	}
	return hash(a.GetSha256(), "artifact.sha256")
}

func ValidateChunkManifest(manifest *runtimev1.ChunkManifest, limits *runtimev1.Limits, maximumTotal uint64, requireInline bool) ([]byte, error) {
	if manifest == nil || !manifest.GetComplete() {
		return nil, fail("chunk", "authoritative transfer manifest must be complete")
	}
	if len(manifest.GetChunks()) > maxTransferChunks {
		return nil, fail("chunk_limit", "transfer exceeds 64 chunks")
	}
	var total uint64
	allInline := true
	artifactHandles := map[string]bool{}
	var body bytes.Buffer
	totalDigest := sha256.New()
	for i, chunk := range manifest.GetChunks() {
		if chunk.GetSequence() != uint32(i) {
			return nil, fail("chunk_sequence", "chunk sequences must be unique and contiguous from zero")
		}
		if chunk.GetStorage() == nil || chunk.GetSizeBytes() == 0 {
			return nil, fail("chunk", "each chunk requires non-empty inline or artifact storage")
		}
		if chunk.GetSizeBytes() > limits.GetMaxArtifactChunkBytes() {
			return nil, fail("chunk_limit", "chunk exceeds max_artifact_chunk_bytes")
		}
		if err := hash(chunk.GetSha256(), "chunk.sha256"); err != nil {
			return nil, err
		}
		switch storage := chunk.GetStorage().(type) {
		case *runtimev1.DataChunk_InlineBody:
			if uint64(len(storage.InlineBody)) != chunk.GetSizeBytes() {
				return nil, fail("chunk", "inline chunk size metadata disagrees")
			}
			if uint64(len(storage.InlineBody)) > limits.GetMaxInlineBodyBytes() {
				return nil, fail("body_limit", "inline chunk exceeds max_inline_body_bytes")
			}
			sum := sha256.Sum256(storage.InlineBody)
			if hex.EncodeToString(sum[:]) != chunk.GetSha256() {
				return nil, fail("hash", "inline chunk digest disagrees")
			}
			_, _ = totalDigest.Write(storage.InlineBody)
			if requireInline {
				_, _ = body.Write(storage.InlineBody)
			}
		case *runtimev1.DataChunk_Artifact:
			allInline = false
			if err := ValidateArtifact(storage.Artifact, limits); err != nil {
				return nil, err
			}
			if artifactHandles[storage.Artifact.GetHandle()] {
				return nil, fail("duplicate", "artifact chunk handles must be unique")
			}
			artifactHandles[storage.Artifact.GetHandle()] = true
			if storage.Artifact.GetSizeBytes() != chunk.GetSizeBytes() || storage.Artifact.GetSha256() != chunk.GetSha256() {
				return nil, fail("chunk", "artifact handle and chunk metadata disagree")
			}
		default:
			return nil, fail("chunk", "unknown chunk storage")
		}
		total += chunk.GetSizeBytes()
	}
	if total != manifest.GetTotalSizeBytes() || total > maximumTotal {
		return nil, fail("transfer_limit", "chunk total disagrees or exceeds transfer limit")
	}
	if err := hash(manifest.GetTotalSha256(), "chunk.total_sha256"); err != nil {
		return nil, err
	}
	if total == 0 && len(manifest.GetChunks()) > 0 || total > 0 && len(manifest.GetChunks()) == 0 {
		return nil, fail("chunk", "chunk presence disagrees with total size")
	}
	if allInline {
		if hex.EncodeToString(totalDigest.Sum(nil)) != manifest.GetTotalSha256() {
			return nil, fail("hash", "inline transfer total digest disagrees")
		}
		if requireInline {
			if total > limits.GetMaxInlineBodyBytes() {
				return nil, fail("replay", "normalized replay body exceeds bounded inline decode size")
			}
			return body.Bytes(), nil
		}
		return nil, nil
	}
	if requireInline {
		return nil, fail("replay", "offline replay fixture requires inline chunk bytes")
	}
	return nil, nil
}

func ValidateExtension(extension *runtimev1.ExtensionEnvelope, context string) error {
	if extension == nil {
		return fail("extension", "extension is required")
	}
	if err := text(extension.GetSchemaId(), "extension.schema_id", 256); err != nil {
		return err
	}
	if extension.GetEncoding() == 0 || runtimev1.ExtensionEncoding_name[int32(extension.GetEncoding())] == "" {
		return fail("enum", "extension encoding is unspecified or unknown")
	}
	key := fmt.Sprintf("%s|%d|%d", extension.GetSchemaId(), extension.GetSchemaVersion(), extension.GetEncoding())
	if !extensionRegistry[key] {
		return fail("extension", "extension schema/version/encoding is not registered for v1")
	}
	if !extensionContexts[extension.GetSchemaId()][context] {
		return fail("extension_context", extension.GetSchemaId()+" is forbidden in "+context)
	}
	if len(extension.GetPayload()) > maxExtensionBytes {
		return fail("extension_limit", "extension payload exceeds 65536 bytes")
	}
	if err := hash(extension.GetPayloadSha256(), "extension.payload_sha256"); err != nil {
		return err
	}
	sum := sha256.Sum256(extension.GetPayload())
	if hex.EncodeToString(sum[:]) != extension.GetPayloadSha256() {
		return fail("hash", "extension payload digest disagrees")
	}
	if extension.GetEncoding() == runtimev1.ExtensionEncoding_EXTENSION_ENCODING_CANONICAL_JSON {
		var value any
		if err := json.Unmarshal(extension.GetPayload(), &value); err != nil {
			return fail("extension", "canonical JSON extension payload is invalid")
		}
		canonical, _ := json.Marshal(value)
		if !bytes.Equal(canonical, extension.GetPayload()) {
			return fail("extension", "JSON extension payload is not canonical")
		}
		if err := validateExtensionSchema(extension.GetSchemaId(), extension.GetPayload()); err != nil {
			return err
		}
	}
	return nil
}

func validateExtensionSchema(schemaID string, payload []byte) error {
	object := map[string]json.RawMessage{}
	if err := json.Unmarshal(payload, &object); err != nil || object == nil {
		return fail("extension_schema", "extension payload must be a typed JSON object")
	}
	switch schemaID {
	case "jobseek.runtime.v1/representative-json/monitor-config":
		raw, ok := object["pages"]
		if !ok || len(object) != 1 {
			return fail("extension_schema", "monitor-config requires exactly pages")
		}
		var pages uint32
		if json.Unmarshal(raw, &pages) != nil || pages == 0 || pages > 1_000 {
			return fail("extension_schema", "monitor-config pages must be integer 1..1000")
		}
	case "jobseek.runtime.v1/representative-json/scraper-config":
		raw, ok := object["fields"]
		if !ok || len(object) != 1 {
			return fail("extension_schema", "scraper-config requires exactly fields")
		}
		var fields []string
		if json.Unmarshal(raw, &fields) != nil || len(fields) == 0 || len(fields) > 64 {
			return fail("extension_schema", "scraper-config fields are invalid")
		}
		seen := map[string]bool{}
		for _, field := range fields {
			if !scraperExtensionFields[field] || seen[field] {
				return fail("extension_schema", "scraper-config fields are invalid")
			}
			seen[field] = true
		}
	case "jobseek.runtime.v1/representative-json/runtime-metadata":
		raw, ok := object["source"]
		if !ok || len(object) != 1 {
			return fail("extension_schema", "runtime-metadata requires exactly source")
		}
		var source string
		if json.Unmarshal(raw, &source) != nil || (source != "captured-provider" && source != "offline-fixture") {
			return fail("extension_schema", "runtime-metadata source is invalid")
		}
	case "jobseek.runtime.v1/browser/evaluation-json":
		raw, ok := object["value"]
		if !ok || len(object) != 1 {
			return fail("extension_schema", "evaluation-json requires exactly value")
		}
		var value int64
		const maxSafeJSONInteger int64 = 9_007_199_254_740_991
		if json.Unmarshal(raw, &value) != nil || value < -maxSafeJSONInteger || value > maxSafeJSONInteger {
			return fail("extension_schema", "evaluation-json value must be a safe integer")
		}
	default:
		return fail("extension_schema", "registered extension schema has no validator")
	}
	return nil
}

func ValidateExtensions(extensions []*runtimev1.ExtensionEnvelope, context string) error {
	seen := map[string]bool{}
	for _, extension := range extensions {
		if seen[extension.GetSchemaId()] {
			return fail("duplicate", "extension schema IDs must be unique in one envelope set")
		}
		seen[extension.GetSchemaId()] = true
		if err := ValidateExtension(extension, context); err != nil {
			return err
		}
	}
	return nil
}

func errorPolicy(code runtimev1.ErrorCode) runtimev1.ErrorDisposition {
	switch code {
	case runtimev1.ErrorCode_ERROR_CODE_TDM_RESERVED:
		return runtimev1.ErrorDisposition_ERROR_DISPOSITION_DEFER_POLICY
	case runtimev1.ErrorCode_ERROR_CODE_PROVIDER_GONE:
		return runtimev1.ErrorDisposition_ERROR_DISPOSITION_PROVIDER_GONE_POLICY
	case runtimev1.ErrorCode_ERROR_CODE_PERMANENT_GONE:
		return runtimev1.ErrorDisposition_ERROR_DISPOSITION_PERMANENT_GONE_POLICY
	case runtimev1.ErrorCode_ERROR_CODE_INVALID_CONFIG:
		return runtimev1.ErrorDisposition_ERROR_DISPOSITION_INVALID_CONFIG_POLICY
	case runtimev1.ErrorCode_ERROR_CODE_CANCELLED:
		return runtimev1.ErrorDisposition_ERROR_DISPOSITION_CANCELLED_POLICY
	case runtimev1.ErrorCode_ERROR_CODE_AMBIGUOUS_ORIGIN, runtimev1.ErrorCode_ERROR_CODE_UNSUPPORTED_CAPABILITY:
		return runtimev1.ErrorDisposition_ERROR_DISPOSITION_FAIL_CLOSED_POLICY
	default:
		return runtimev1.ErrorDisposition_ERROR_DISPOSITION_RETRY_POLICY
	}
}

func ValidateError(e *runtimev1.RuntimeError, negotiated ...*runtimev1.Limits) error {
	if e == nil || e.GetCode() == runtimev1.ErrorCode_ERROR_CODE_UNSPECIFIED {
		return fail("enum", "error.code is unspecified")
	}
	limits := hardLimits
	if len(negotiated) > 0 && negotiated[0] != nil {
		limits = negotiated[0]
	}
	if _, ok := runtimev1.ErrorCode_name[int32(e.GetCode())]; !ok {
		return fail("enum", "error.code is unknown")
	}
	if e.GetDisposition() == runtimev1.ErrorDisposition_ERROR_DISPOSITION_UNSPECIFIED {
		return fail("enum", "error.disposition is unspecified")
	}
	if _, ok := runtimev1.ErrorDisposition_name[int32(e.GetDisposition())]; !ok {
		return fail("enum", "error.disposition is unknown")
	}
	if expected := errorPolicy(e.GetCode()); e.GetDisposition() != expected {
		return fail("error_policy", fmt.Sprintf("%s requires %s", e.GetCode(), expected))
	}
	if len([]byte(e.GetMessage())) > 4096 {
		return fail("error_limit", "error.message exceeds 4096 bytes")
	}
	if e.GetCode() == runtimev1.ErrorCode_ERROR_CODE_HTTP_STATUS && (e.HttpStatus == nil || e.GetHttpStatus() < 100 || e.GetHttpStatus() > 599) {
		return fail("http_status", "HTTP_STATUS requires status 100..599")
	}
	if e.HttpStatus != nil && (e.GetHttpStatus() < 100 || e.GetHttpStatus() > 599) {
		return fail("http_status", "http status out of range")
	}
	if e.RetryAfterMs != nil && e.GetRetryAfterMs() > limits.GetMaxRetryAfterMs() {
		return fail("limit", "retry_after_ms exceeds independent scheduling-hint limit")
	}
	detailKeys := map[string]bool{}
	for _, detail := range e.GetDiagnosticDetails() {
		if err := text(detail.GetKey(), "error.diagnostic_details.key", 128); err != nil {
			return err
		}
		if detailKeys[detail.GetKey()] {
			return fail("duplicate", "diagnostic detail keys must be unique")
		}
		detailKeys[detail.GetKey()] = true
		if len([]byte(detail.GetValue())) > 2_048 {
			return fail("diagnostic_limit", "diagnostic detail exceeds 2048 bytes")
		}
	}
	return nil
}

func ValidateOriginOperations(ops []*runtimev1.OriginOperationRef, primary string) (map[string]*runtimev1.OriginOperationRef, error) {
	if len(ops) == 0 {
		return nil, fail("origin", "at least one semantic origin operation is required")
	}
	result := map[string]*runtimev1.OriginOperationRef{}
	for i, op := range ops {
		if err := text(op.GetOriginRequestId(), "origin_request_id", 512); err != nil {
			return nil, err
		}
		if err := text(op.GetRole(), "origin.role", 128); err != nil {
			return nil, err
		}
		if err := hash(op.GetRequestFingerprint(), "origin.request_fingerprint"); err != nil {
			return nil, err
		}
		if op.GetOperationSequence() != uint32(i) {
			return nil, fail("origin_sequence", "origin sequences must be contiguous")
		}
		if _, ok := result[op.GetOriginRequestId()]; ok {
			return nil, fail("duplicate", "duplicate origin_request_id")
		}
		if op.ParentOriginRequestId != nil {
			if _, ok := result[op.GetParentOriginRequestId()]; !ok {
				return nil, fail("origin_parent", "parent must reference earlier operation")
			}
		}
		result[op.GetOriginRequestId()] = op
	}
	if primary != "" && ops[0].GetOriginRequestId() != primary {
		return nil, fail("origin", "request origin ID must equal first operation")
	}
	return result, nil
}

func ValidateManifest(m *runtimev1.BoardManifest) error {
	if m == nil {
		return fail("manifest", "manifest is required")
	}
	if err := contract(m.GetContractVersion()); err != nil {
		return err
	}
	for field, value := range map[string]string{
		"manifest_id": m.GetManifestId(), "board_id": m.GetBoardId(), "company_id": m.GetCompanyId(),
		"config_revision": m.GetConfigRevision(), "config_fingerprint": m.GetConfigFingerprint(),
		"provider_family": m.GetProviderFamily(), "monitor_type": m.GetMonitorType(), "throttle_key": m.GetThrottleKey(),
	} {
		if err := text(value, "manifest."+field, 512); err != nil {
			return err
		}
	}
	if err := hash(m.GetConfigFingerprint(), "manifest.config_fingerprint"); err != nil {
		return err
	}
	if err := validURL(m.GetBoardUrl(), "manifest.board_url"); err != nil {
		return err
	}
	if m.GetCheckIntervalMs() == 0 || m.GetScrapeIntervalMs() == 0 {
		return fail("manifest", "manifest intervals must be positive")
	}
	if m.ScraperType != nil {
		if err := text(m.GetScraperType(), "manifest.scraper_type", 128); err != nil {
			return err
		}
	}
	return ValidateExtensions(m.GetConfigExtensions(), "manifest")
}

func ValidateRequest(r *runtimev1.ExecutionRequest) error {
	if r == nil {
		return fail("request", "request is required")
	}
	if err := contract(r.GetContractVersion()); err != nil {
		return err
	}
	for field, value := range map[string]string{"request_id": r.GetRequestId(), "origin_request_id": r.GetOriginRequestId(), "attempt_id": r.GetAttemptId()} {
		if err := text(value, "request."+field, 512); err != nil {
			return err
		}
	}
	if _, ok := runtimev1.ExecutionKind_name[int32(r.GetKind())]; !ok || r.GetKind() == runtimev1.ExecutionKind_EXECUTION_KIND_UNSPECIFIED {
		return fail("enum", "request.kind is unknown")
	}
	if !deadlineRE.MatchString(r.GetDeadlineRfc3339()) {
		return fail("deadline", "deadline is not strict RFC3339")
	}
	if _, err := time.Parse(time.RFC3339Nano, r.GetDeadlineRfc3339()); err != nil {
		return fail("deadline", "deadline is not RFC3339 with offset")
	}
	if err := validateTraceContext(r.Traceparent, r.Tracestate); err != nil {
		return err
	}
	if err := ValidateManifest(r.GetBoardManifest()); err != nil {
		return err
	}
	if err := ValidateFencing(r.GetFencingContext(), r.GetBoardManifest().GetConfigRevision()); err != nil {
		return err
	}
	if _, err := ValidateOriginOperations(r.GetOriginOperations(), r.GetOriginRequestId()); err != nil {
		return err
	}
	switch r.GetKind() {
	case runtimev1.ExecutionKind_EXECUTION_KIND_MONITOR:
		input, ok := r.GetInput().(*runtimev1.ExecutionRequest_Monitor)
		if !ok || input.Monitor.GetMonitorType() != r.GetBoardManifest().GetMonitorType() {
			return fail("kind", "monitor request/input/manifest disagree")
		}
	case runtimev1.ExecutionKind_EXECUTION_KIND_SCRAPE:
		input, ok := r.GetInput().(*runtimev1.ExecutionRequest_Scrape)
		if !ok {
			return fail("kind", "scrape input required")
		}
		if err := validURL(input.Scrape.GetSourceUrl(), "scrape.source_url"); err != nil {
			return err
		}
		if r.GetBoardManifest().ScraperType == nil || input.Scrape.GetScraperType() != r.GetBoardManifest().GetScraperType() {
			return fail("kind", "scraper type mismatch")
		}
	case runtimev1.ExecutionKind_EXECUTION_KIND_BROWSER:
		input, ok := r.GetInput().(*runtimev1.ExecutionRequest_Browser)
		if !ok {
			return fail("kind", "browser input required")
		}
		if err := ValidateBrowserPlan(input.Browser.GetPlan(), nil); err != nil {
			return err
		}
		planOps := input.Browser.GetPlan().GetOriginOperations()
		if len(planOps) != len(r.GetOriginOperations()) {
			return fail("origin", "browser plan operations must equal execution operations")
		}
		for i, op := range planOps {
			if !bytes.Equal(deterministicBytes(op), deterministicBytes(r.GetOriginOperations()[i])) {
				return fail("origin", "browser plan operations must equal execution operations")
			}
		}
	}
	return nil
}

func ValidateJobContent(content *runtimev1.JobContent, limits *runtimev1.Limits) error {
	if content == nil {
		return fail("body", "JobContent is required")
	}
	meaningful := func(value *string, field string) (bool, error) {
		if value == nil {
			return false, nil
		}
		normalized := strings.TrimSpace(*value)
		visible := strings.TrimSpace(regexp.MustCompile(`<[^>]*>`).ReplaceAllString(normalized, ""))
		if visible == "" || sentinelText[strings.ToLower(visible)] {
			return false, fail("job_content", field+" must not be empty, whitespace, or sentinel")
		}
		return true, nil
	}
	titlePresent, err := meaningful(content.Title, "job.title")
	if err != nil {
		return err
	}
	descriptionPresent, err := meaningful(content.DescriptionHtml, "job.description_html")
	if err != nil {
		return err
	}
	if content.Language != nil && !canonicalLocale(content.GetLanguage()) {
		return fail("locale", "job.language must be canonical and non-alias")
	}
	if content.BaseSalary != nil {
		salary := content.GetBaseSalary()
		if err := text(salary.GetCurrency(), "salary.currency", 3); err != nil {
			return err
		}
		if err := text(salary.GetPeriod(), "salary.period", 32); err != nil {
			return err
		}
		if salary.MinimumMinor != nil && salary.MaximumMinor != nil && salary.GetMinimumMinor() > salary.GetMaximumMinor() {
			return fail("salary", "salary minimum exceeds maximum")
		}
	}
	locales := map[string]bool{}
	for _, localized := range content.GetLocalizations() {
		if err := text(localized.GetLocale(), "localization.locale", 35); err != nil {
			return err
		}
		if locales[localized.GetLocale()] {
			return fail("duplicate", "localized content locales must be unique")
		}
		locales[localized.GetLocale()] = true
		if !canonicalLocale(localized.GetLocale()) {
			return fail("locale", "localization locale must be canonical and non-alias")
		}
		localizedTitle, err := meaningful(localized.Title, "localization.title")
		if err != nil {
			return err
		}
		localizedDescription, err := meaningful(localized.DescriptionHtml, "localization.description_html")
		if err != nil {
			return err
		}
		if !localizedTitle && !localizedDescription {
			return fail("job_content", "localization must contain title or description")
		}
		titlePresent = titlePresent || localizedTitle
		descriptionPresent = descriptionPresent || localizedDescription
	}
	if !titlePresent || !descriptionPresent {
		return fail("job_content", "rich job requires meaningful title and description")
	}
	seenSkills := map[string]bool{}
	for _, skill := range content.GetSkills() {
		if skill == "" || skill != strings.TrimSpace(skill) || seenSkills[skill] {
			return fail("job_content", "skills must be unique, nonempty, and trimmed")
		}
		seenSkills[skill] = true
	}
	if content.Locations != nil {
		seenLocations := map[string]bool{}
		for _, location := range content.GetLocations().GetValues() {
			if location == "" || location != strings.TrimSpace(location) || seenLocations[location] {
				return fail("job_content", "locations must be unique, nonempty, and trimmed")
			}
			seenLocations[location] = true
		}
	}
	if err := ValidateExtensions(content.GetExtensions(), "job_content"); err != nil {
		return err
	}
	raw, _ := proto.Marshal(content)
	if uint64(len(raw)) > limits.GetMaxInlineBodyBytes() {
		return fail("body_limit", "JobContent exceeds max_inline_body_bytes")
	}
	return nil
}

func ValidateMonitorResult(result *runtimev1.MonitorResult, limits *runtimev1.Limits) error {
	if result == nil {
		return fail("body", "MonitorResult is required")
	}
	urls := map[string]bool{}
	for _, value := range result.GetUrls() {
		if urls[value] {
			return fail("duplicate", "monitor URLs must be unique")
		}
		urls[value] = true
		if err := validURL(value, "monitor.urls"); err != nil {
			return err
		}
	}
	jobs := map[string]bool{}
	for _, job := range result.GetJobs() {
		if err := validURL(job.GetUrl(), "monitor.jobs.url"); err != nil {
			return err
		}
		if jobs[job.GetUrl()] {
			return fail("duplicate", "job URLs must be unique")
		}
		if !urls[job.GetUrl()] {
			return fail("url_job", "job URL is absent from urls")
		}
		jobs[job.GetUrl()] = true
		if err := ValidateJobContent(job.GetContent(), limits); err != nil {
			return err
		}
	}
	if len(jobs) > 0 && !result.GetHybrid() && len(jobs) != len(urls) {
		return fail("url_job", "non-hybrid rich result requires all jobs")
	}
	if result.NewSitemapUrl != nil {
		if err := validURL(result.GetNewSitemapUrl(), "monitor.new_sitemap_url"); err != nil {
			return err
		}
	}
	if result.MetadataUpdates != nil {
		if err := ValidateExtensions(result.GetMetadataUpdates().GetExtensions(), "monitor_metadata"); err != nil {
			return err
		}
	}
	raw, _ := proto.Marshal(result)
	if uint64(len(raw)) > limits.GetMaxInlineBodyBytes() {
		return fail("body_limit", "monitor result exceeds max_inline_body_bytes")
	}
	return nil
}

func validateSelector(selector *runtimev1.Selector, field string) error {
	if selector == nil || selector.GetKind() == 0 || runtimev1.SelectorKind_name[int32(selector.GetKind())] == "" {
		return fail("enum", field+" kind is unspecified or unknown")
	}
	if err := text(selector.GetValue(), field+".value", 4_096); err != nil {
		return err
	}
	if selector.FrameName != nil {
		return text(selector.GetFrameName(), field+".frame_name", 256)
	}
	return nil
}

func validateBrowserTimeout(value uint64, field string, limits *runtimev1.Limits) error {
	if value == 0 || value > limits.GetMaxActiveDurationMs() {
		return fail("limit", field+" is out of bounds")
	}
	return nil
}

func ValidateBrowserPlan(plan *runtimev1.BrowserPlan, limits *runtimev1.Limits) error {
	if plan == nil {
		return fail("browser_plan", "plan is required")
	}
	if limits == nil {
		limits = hardLimits
	}
	if err := ValidateLimits(limits, nil); err != nil {
		return err
	}
	if err := contract(plan.GetContractVersion()); err != nil {
		return err
	}
	if err := validURL(plan.GetTargetUrl(), "browser.target_url"); err != nil {
		return err
	}
	caps := map[runtimev1.BrowserCapability]bool{}
	for _, cap := range plan.GetRequiredCapabilities() {
		if cap == 0 || runtimev1.BrowserCapability_name[int32(cap)] == "" {
			return fail("enum", "unknown browser capability")
		}
		if caps[cap] {
			return fail("duplicate", "browser capabilities must be unique")
		}
		caps[cap] = true
	}
	if !caps[runtimev1.BrowserCapability_BROWSER_CAPABILITY_RENDER] {
		return fail("capability", "browser navigation requires render capability")
	}
	ops, err := ValidateOriginOperations(plan.GetOriginOperations(), "")
	if err != nil {
		return err
	}
	if _, ok := ops[plan.GetNavigation().GetOriginRequestId()]; !ok {
		return fail("origin", "navigation origin ID is undeclared")
	}
	originOwners := []string{plan.GetNavigation().GetOriginRequestId()}
	if plan.GetNavigation().GetWaitUntil() == 0 || runtimev1.WaitCondition_name[int32(plan.GetNavigation().GetWaitUntil())] == "" {
		return fail("enum", "wait condition unspecified")
	}
	if plan.GetNavigation().GetTimeoutMs() == 0 || plan.GetNavigation().GetTimeoutMs() > limits.GetMaxActiveDurationMs() {
		return fail("limit", "navigation timeout out of bounds")
	}
	if err := headers(plan.GetNavigation().GetHeaders(), "browser.navigation.headers"); err != nil {
		return err
	}
	if uint32(len(plan.GetActions())) > limits.GetMaxBrowserActions() || uint32(len(plan.GetCaptures())) > limits.GetMaxBrowserCaptures() || uint32(len(plan.GetEvaluations())) > limits.GetMaxBrowserEvaluations() {
		return fail("limit", "browser plan item count exceeds limits")
	}
	ids := map[string]bool{}
	usesFrames := false
	for _, action := range plan.GetActions() {
		if err := text(action.GetActionId(), "browser.action_id", 128); err != nil {
			return err
		}
		if ids[action.GetActionId()] || action.GetAction() == nil {
			return fail("browser_action", "action IDs/tags invalid")
		}
		ids[action.GetActionId()] = true
		if action.GetNetworkEffect() == 0 || runtimev1.BrowserNetworkEffect_name[int32(action.GetNetworkEffect())] == "" {
			return fail("enum", "browser action network effect is unspecified or unknown")
		}
		hasOrigin := action.OriginRequestId != nil
		if hasOrigin {
			if _, ok := ops[action.GetOriginRequestId()]; !ok {
				return fail("origin", "action origin ID undeclared")
			}
		}
		if action.GetNetworkEffect() == runtimev1.BrowserNetworkEffect_BROWSER_NETWORK_EFFECT_ORIGIN_CONTACT {
			if !hasOrigin {
				return fail("origin", "origin-contact action requires stable origin ID")
			}
			originOwners = append(originOwners, action.GetOriginRequestId())
		} else if hasOrigin {
			return fail("origin", "no-network action must omit origin ID")
		}
		switch item := action.GetAction().(type) {
		case *runtimev1.BrowserAction_Click:
			if err := validateSelector(item.Click.GetSelector(), "browser.action.click.selector"); err != nil {
				return err
			}
			if err := validateBrowserTimeout(item.Click.GetTimeoutMs(), "browser.action.click.timeout_ms", limits); err != nil {
				return err
			}
			usesFrames = usesFrames || item.Click.GetSelector().FrameName != nil
		case *runtimev1.BrowserAction_Fill:
			if err := validateSelector(item.Fill.GetSelector(), "browser.action.fill.selector"); err != nil {
				return err
			}
			if len([]byte(item.Fill.GetValue())) > 65_536 {
				return fail("limit", "browser fill value exceeds 65536 bytes")
			}
			if err := validateBrowserTimeout(item.Fill.GetTimeoutMs(), "browser.action.fill.timeout_ms", limits); err != nil {
				return err
			}
			usesFrames = usesFrames || item.Fill.GetSelector().FrameName != nil
		case *runtimev1.BrowserAction_Wait:
			if item.Wait.Selector != nil {
				if err := validateSelector(item.Wait.GetSelector(), "browser.action.wait.selector"); err != nil {
					return err
				}
				usesFrames = usesFrames || item.Wait.GetSelector().FrameName != nil
			}
			if item.Wait.GetDurationMs() > limits.GetMaxActiveDurationMs() {
				return fail("limit", "browser wait duration is out of bounds")
			}
			if err := validateBrowserTimeout(item.Wait.GetTimeoutMs(), "browser.action.wait.timeout_ms", limits); err != nil {
				return err
			}
		case *runtimev1.BrowserAction_Scroll:
			if item.Scroll.GetDirection() == 0 || runtimev1.ScrollDirection_name[int32(item.Scroll.GetDirection())] == "" {
				return fail("enum", "browser scroll direction is unspecified or unknown")
			}
			if item.Scroll.GetPixels() == 0 || item.Scroll.GetPixels() > 1_000_000 {
				return fail("limit", "browser scroll pixels are out of bounds")
			}
		case *runtimev1.BrowserAction_Paginate:
			if action.GetNetworkEffect() != runtimev1.BrowserNetworkEffect_BROWSER_NETWORK_EFFECT_ORIGIN_CONTACT {
				return fail("origin", "pagination is origin-contact work and requires a bound operation")
			}
			if err := validateSelector(item.Paginate.GetNextSelector(), "browser.action.paginate.next_selector"); err != nil {
				return err
			}
			if item.Paginate.GetMaxPages() == 0 || item.Paginate.GetMaxPages() > 1_000 {
				return fail("limit", "browser pagination page count is out of bounds")
			}
			if item.Paginate.GetDynamicOriginPerAdditionalPage() {
				return fail("origin", "v1 disallows dynamic pagination origin allocation")
			}
			pageOrigins := item.Paginate.GetAdditionalPageOriginRequestIds()
			if len(pageOrigins) != int(item.Paginate.GetMaxPages()-1) {
				return fail("origin", "pagination requires exactly max_pages-1 predeclared page origins")
			}
			seenPageOrigins := map[string]bool{}
			for _, originID := range pageOrigins {
				if ops[originID] == nil {
					return fail("origin", "pagination page origin is undeclared")
				}
				if seenPageOrigins[originID] {
					return fail("duplicate", "pagination page origins must be unique")
				}
				seenPageOrigins[originID] = true
				originOwners = append(originOwners, originID)
			}
			if err := validateBrowserTimeout(item.Paginate.GetPageTimeoutMs(), "browser.action.paginate.page_timeout_ms", limits); err != nil {
				return err
			}
			usesFrames = usesFrames || item.Paginate.GetNextSelector().FrameName != nil
			if !caps[runtimev1.BrowserCapability_BROWSER_CAPABILITY_PAGINATION] {
				return fail("capability", "pagination capability missing")
			}
		case *runtimev1.BrowserAction_Evaluate:
			if err := text(item.Evaluate.GetExpression(), "browser.action.evaluate.expression", 262_144); err != nil {
				return err
			}
			if err := validateBrowserTimeout(item.Evaluate.GetTimeoutMs(), "browser.action.evaluate.timeout_ms", limits); err != nil {
				return err
			}
			if item.Evaluate.GetMaxResultBytes() == 0 || item.Evaluate.GetMaxResultBytes() > limits.GetMaxBrowserTransferBytes() {
				return fail("limit", "browser evaluate result bound is invalid")
			}
			if !caps[runtimev1.BrowserCapability_BROWSER_CAPABILITY_EVALUATE] {
				return fail("capability", "evaluate capability missing")
			}
			usesFrames = usesFrames || item.Evaluate.FrameName != nil
		}
	}
	if len(plan.GetActions()) > 0 && !caps[runtimev1.BrowserCapability_BROWSER_CAPABILITY_ACTIONS] {
		return fail("capability", "browser actions require actions capability")
	}
	captureIDs := map[string]bool{}
	for _, capture := range plan.GetCaptures() {
		if err := text(capture.GetCaptureId(), "browser.capture_id", 128); err != nil {
			return err
		}
		if captureIDs[capture.GetCaptureId()] {
			return fail("duplicate", "browser capture IDs must be unique")
		}
		captureIDs[capture.GetCaptureId()] = true
		if capture.GetKind() == 0 || runtimev1.CaptureKind_name[int32(capture.GetKind())] == "" {
			return fail("enum", "browser capture kind is unspecified or unknown")
		}
		if capture.UrlPattern != nil {
			if err := text(capture.GetUrlPattern(), "browser.capture.url_pattern", 4_096); err != nil {
				return err
			}
		}
		if capture.GetMaxBytes() == 0 || capture.GetMaxBytes() > limits.GetMaxBrowserTransferBytes() {
			return fail("limit", "browser capture max_bytes is invalid")
		}
	}
	evaluationIDs := map[string]bool{}
	for _, evaluation := range plan.GetEvaluations() {
		if err := text(evaluation.GetEvaluationId(), "browser.evaluation_id", 128); err != nil {
			return err
		}
		if evaluationIDs[evaluation.GetEvaluationId()] {
			return fail("duplicate", "browser evaluation IDs must be unique")
		}
		evaluationIDs[evaluation.GetEvaluationId()] = true
		if err := text(evaluation.GetExpression(), "browser.evaluation.expression", 262_144); err != nil {
			return err
		}
		if evaluation.GetMaxResultBytes() == 0 || evaluation.GetMaxResultBytes() > limits.GetMaxBrowserTransferBytes() {
			return fail("limit", "browser evaluation max_result_bytes is invalid")
		}
		if evaluation.GetNetworkEffect() == 0 || runtimev1.BrowserNetworkEffect_name[int32(evaluation.GetNetworkEffect())] == "" {
			return fail("enum", "browser evaluation network effect is unspecified or unknown")
		}
		usesFrames = usesFrames || evaluation.FrameName != nil
		hasOrigin := evaluation.OriginRequestId != nil
		if evaluation.GetNetworkEffect() == runtimev1.BrowserNetworkEffect_BROWSER_NETWORK_EFFECT_ORIGIN_CONTACT {
			if !hasOrigin {
				return fail("origin", "origin-contact evaluation requires origin ID")
			}
			if ops[evaluation.GetOriginRequestId()] == nil {
				return fail("origin", "evaluation references undeclared origin operation")
			}
			originOwners = append(originOwners, evaluation.GetOriginRequestId())
		} else if hasOrigin {
			return fail("origin", "no-network evaluation must omit origin ID")
		}
	}
	seenOriginOwners := map[string]bool{}
	for _, id := range originOwners {
		if seenOriginOwners[id] {
			return fail("origin", "browser origin operation IDs must have exactly one plan owner")
		}
		seenOriginOwners[id] = true
	}
	if len(seenOriginOwners) != len(ops) {
		return fail("origin", "browser origin operations must be exhausted by navigation/actions/evaluations")
	}
	for _, interception := range plan.GetInterceptions() {
		if err := text(interception.GetUrlPattern(), "browser.interception.url_pattern", 4_096); err != nil {
			return err
		}
		if err := headers(interception.GetReplaceHeaders(), "browser.interception.headers"); err != nil {
			return err
		}
	}
	if len(plan.GetEvaluations()) > 0 && !caps[runtimev1.BrowserCapability_BROWSER_CAPABILITY_EVALUATE] {
		return fail("capability", "browser evaluations require evaluate capability")
	}
	if len(plan.GetCaptures()) > 0 && !caps[runtimev1.BrowserCapability_BROWSER_CAPABILITY_RESPONSE_CAPTURE] {
		return fail("capability", "browser captures require response-capture capability")
	}
	if len(plan.GetInterceptions()) > 0 && !caps[runtimev1.BrowserCapability_BROWSER_CAPABILITY_REQUEST_INTERCEPTION] {
		return fail("capability", "interception rules require request-interception capability")
	}
	if usesFrames && !caps[runtimev1.BrowserCapability_BROWSER_CAPABILITY_FRAMES] {
		return fail("capability", "frame_name requires frames capability")
	}
	if (len(plan.GetNavigation().GetHeaders()) > 0 || plan.GetNavigation().GetIgnoreTlsErrors()) && !caps[runtimev1.BrowserCapability_BROWSER_CAPABILITY_TRANSPORT_OVERRIDES] {
		return fail("capability", "navigation transport overrides require capability")
	}
	session := plan.GetSession()
	if session != nil && session.SessionKey != nil {
		if err := text(session.GetSessionKey(), "browser.session.session_key", 512); err != nil {
			return err
		}
	}
	if session != nil && session.ProxyPolicyRef != nil {
		if err := text(session.GetProxyPolicyRef(), "browser.session.proxy_policy_ref", 512); err != nil {
			return err
		}
	}
	if session.GetPersistent() && (session == nil || session.SessionKey == nil) {
		return fail("browser_session", "persistent browser sessions require a stable session_key")
	}
	if session.GetHeadfulIdentity() && !caps[runtimev1.BrowserCapability_BROWSER_CAPABILITY_HEADFUL_IDENTITY] {
		return fail("capability", "headful identity requires capability")
	}
	if session != nil && session.ProxyPolicyRef != nil && !caps[runtimev1.BrowserCapability_BROWSER_CAPABILITY_PROXY] {
		return fail("capability", "proxy policy requires proxy capability")
	}
	if session.GetPersistent() && !caps[runtimev1.BrowserCapability_BROWSER_CAPABILITY_PERSISTENT_SESSION] {
		return fail("capability", "persistent session capability missing")
	}
	return nil
}

func ValidateBrowserResult(result *runtimev1.BrowserResult, limits *runtimev1.Limits, plans ...*runtimev1.BrowserPlan) error {
	if result == nil {
		return fail("browser_union", "result required")
	}
	if limits == nil {
		limits = hardLimits
	}
	if err := contract(result.GetContractVersion()); err != nil {
		return err
	}
	if result.GetBackend() == 0 || runtimev1.BrowserBackend_name[int32(result.GetBackend())] == "" {
		return fail("enum", "browser backend unspecified")
	}
	var plan *runtimev1.BrowserPlan
	if len(plans) > 0 {
		plan = plans[0]
	}
	artifactHandles := map[string]bool{}
	var artifactBytes uint64
	accountArtifact := func(artifact *runtimev1.ArtifactHandle) error {
		if err := ValidateArtifact(artifact, limits); err != nil {
			return err
		}
		if artifactHandles[artifact.GetHandle()] {
			return fail("duplicate", "browser artifact handles must be unique")
		}
		artifactHandles[artifact.GetHandle()] = true
		artifactBytes += artifact.GetSizeBytes()
		return nil
	}
	accountManifest := func(manifest *runtimev1.ChunkManifest) error {
		for _, chunk := range manifest.GetChunks() {
			if artifact := chunk.GetArtifact(); artifact != nil {
				if err := accountArtifact(artifact); err != nil {
					return err
				}
			}
		}
		return nil
	}
	switch outcome := result.GetOutcome().(type) {
	case *runtimev1.BrowserResult_Success:
		if err := validURL(outcome.Success.GetFinalUrl(), "browser.final_url"); err != nil {
			return err
		}
		if outcome.Success.Status != nil && (outcome.Success.GetStatus() < 100 || outcome.Success.GetStatus() > 599) {
			return fail("http_status", "browser status out of range")
		}
		var total uint64
		actionIDs := map[string]bool{}
		for _, item := range outcome.Success.GetActionOutcomes() {
			if err := text(item.GetActionId(), "browser.result.action_id", 128); err != nil {
				return err
			}
			if item.GetDurationMs() > limits.GetMaxActiveDurationMs() {
				return fail("limit", "browser action duration exceeds the active-duration limit")
			}
			if actionIDs[item.GetActionId()] {
				return fail("browser_result", "browser action outcome IDs must be unique")
			}
			actionIDs[item.GetActionId()] = true
			if !item.GetCompleted() {
				return fail("browser_result", "all required browser actions must complete")
			}
		}
		captureIDs := map[string]bool{}
		for _, item := range outcome.Success.GetCaptures() {
			if err := text(item.GetCaptureId(), "browser.result.capture_id", 128); err != nil {
				return err
			}
			if captureIDs[item.GetCaptureId()] {
				return fail("browser_result", "browser capture outcome IDs must be unique")
			}
			captureIDs[item.GetCaptureId()] = true
		}
		evaluationIDs := map[string]bool{}
		for _, item := range outcome.Success.GetEvaluations() {
			if err := text(item.GetEvaluationId(), "browser.result.evaluation_id", 128); err != nil {
				return err
			}
			if evaluationIDs[item.GetEvaluationId()] {
				return fail("browser_result", "browser evaluation outcome IDs must be unique")
			}
			evaluationIDs[item.GetEvaluationId()] = true
			if err := ValidateExtension(item.GetValue(), "browser_evaluation"); err != nil {
				return err
			}
		}
		planCaptures := map[string]*runtimev1.CapturePlan{}
		planEvaluations := map[string]*runtimev1.EvaluationPlan{}
		if plan != nil {
			if len(actionIDs) != len(plan.GetActions()) || len(captureIDs) != len(plan.GetCaptures()) || len(evaluationIDs) != len(plan.GetEvaluations()) {
				return fail("browser_result", "browser outcome ID sets do not match the plan")
			}
			for _, item := range plan.GetActions() {
				if !actionIDs[item.GetActionId()] {
					return fail("browser_result", "browser action outcomes do not match the plan")
				}
			}
			for _, item := range plan.GetCaptures() {
				planCaptures[item.GetCaptureId()] = item
				if !captureIDs[item.GetCaptureId()] {
					return fail("browser_result", "browser capture outcomes do not match the plan")
				}
			}
			for _, item := range plan.GetEvaluations() {
				planEvaluations[item.GetEvaluationId()] = item
				if !evaluationIDs[item.GetEvaluationId()] {
					return fail("browser_result", "browser evaluation outcomes do not match the plan")
				}
			}
		}
		if outcome.Success.Html != nil {
			if _, err := ValidateChunkManifest(outcome.Success.GetHtml(), limits, limits.GetMaxBrowserTransferBytes(), false); err != nil {
				return err
			}
			if err := accountManifest(outcome.Success.GetHtml()); err != nil {
				return err
			}
			total += outcome.Success.GetHtml().GetTotalSizeBytes()
		}
		for _, capture := range outcome.Success.GetCaptures() {
			maximum := limits.GetMaxBrowserTransferBytes()
			planned := planCaptures[capture.GetCaptureId()]
			if planned != nil {
				maximum = planned.GetMaxBytes()
			}
			if _, err := ValidateChunkManifest(capture.GetBody(), limits, maximum, false); err != nil {
				return err
			}
			if planned != nil && planned.GetArtifactOnly() {
				for _, chunk := range capture.GetBody().GetChunks() {
					if _, inline := chunk.GetStorage().(*runtimev1.DataChunk_InlineBody); inline {
						return fail("browser_result", "artifact-only capture returned inline bytes")
					}
				}
			}
			if err := accountManifest(capture.GetBody()); err != nil {
				return err
			}
			total += capture.GetBody().GetTotalSizeBytes()
		}
		for _, evaluation := range outcome.Success.GetEvaluations() {
			if planned := planEvaluations[evaluation.GetEvaluationId()]; planned != nil && uint64(len(evaluation.GetValue().GetPayload())) > planned.GetMaxResultBytes() {
				return fail("transfer_limit", "browser evaluation exceeds its planned byte limit")
			}
			total += uint64(len(evaluation.GetValue().GetPayload()))
		}
		for _, artifact := range outcome.Success.GetArtifacts() {
			if err := accountArtifact(artifact); err != nil {
				return err
			}
			total += artifact.GetSizeBytes()
		}
		if total > limits.GetMaxBrowserTransferBytes() {
			return fail("transfer_limit", "aggregate browser output exceeds browser transfer limit")
		}
	case *runtimev1.BrowserResult_Error:
		if err := ValidateError(outcome.Error.GetError(), limits); err != nil {
			return err
		}
		for _, artifact := range outcome.Error.GetDiagnosticArtifacts() {
			if err := accountArtifact(artifact); err != nil {
				return err
			}
		}
	case *runtimev1.BrowserResult_Unsupported:
		if len(outcome.Unsupported.GetCapabilities()) == 0 {
			return fail("browser_union", "unsupported requires capabilities")
		}
		unsupportedCaps := map[runtimev1.BrowserCapability]bool{}
		for _, cap := range outcome.Unsupported.GetCapabilities() {
			if cap == 0 || runtimev1.BrowserCapability_name[int32(cap)] == "" {
				return fail("enum", "unsupported capability unspecified")
			}
			if unsupportedCaps[cap] {
				return fail("duplicate", "unsupported browser capabilities must be unique")
			}
			unsupportedCaps[cap] = true
			if plan != nil {
				required := false
				for _, candidate := range plan.GetRequiredCapabilities() {
					required = required || candidate == cap
				}
				if !required {
					return fail("capability", "unsupported capability was not required by the plan")
				}
			}
		}
		for _, artifact := range outcome.Unsupported.GetDiagnosticArtifacts() {
			if err := accountArtifact(artifact); err != nil {
				return err
			}
		}
	default:
		return fail("browser_union", "BrowserResult requires tagged outcome")
	}
	if uint32(len(artifactHandles)) > limits.GetMaxArtifactCount() || artifactBytes > limits.GetMaxArtifactTotalBytes() || artifactBytes > limits.GetMaxBrowserTransferBytes() {
		return fail("artifact_limit", "browser artifacts exceed negotiated aggregate limits")
	}
	return nil
}

func BrowserArtifacts(result *runtimev1.BrowserResult) []*runtimev1.ArtifactHandle {
	artifacts := []*runtimev1.ArtifactHandle{}
	switch outcome := result.GetOutcome().(type) {
	case *runtimev1.BrowserResult_Success:
		manifests := []*runtimev1.ChunkManifest{}
		if outcome.Success.Html != nil {
			manifests = append(manifests, outcome.Success.GetHtml())
		}
		for _, capture := range outcome.Success.GetCaptures() {
			manifests = append(manifests, capture.GetBody())
		}
		for _, manifest := range manifests {
			for _, chunk := range manifest.GetChunks() {
				if artifact := chunk.GetArtifact(); artifact != nil {
					artifacts = append(artifacts, artifact)
				}
			}
		}
		artifacts = append(artifacts, outcome.Success.GetArtifacts()...)
	case *runtimev1.BrowserResult_Error:
		artifacts = append(artifacts, outcome.Error.GetDiagnosticArtifacts()...)
	case *runtimev1.BrowserResult_Unsupported:
		artifacts = append(artifacts, outcome.Unsupported.GetDiagnosticArtifacts()...)
	}
	return artifacts
}

func deterministicBytes(m proto.Message) []byte {
	raw, _ := proto.MarshalOptions{Deterministic: true}.Marshal(m)
	return raw
}

func semanticFrameBytes(frame *runtimev1.ExecutionFrame) []byte {
	value := proto.Clone(frame).(*runtimev1.ExecutionFrame)
	value.AttemptId = ""
	return deterministicBytes(value)
}

func UvarintSize(value uint64) uint64 {
	size := uint64(1)
	for value >= 0x80 {
		value >>= 7
		size++
	}
	return size
}

func EncodeRecord(payload []byte, maximum uint64) ([]byte, error) {
	size := uint64(len(payload))
	if size+UvarintSize(size) > maximum {
		return nil, fail("frame_limit", "record exceeds max_frame_bytes")
	}
	prefix := make([]byte, binary.MaxVarintLen64)
	n := binary.PutUvarint(prefix, size)
	result := make([]byte, n+len(payload))
	copy(result, prefix[:n])
	copy(result[n:], payload)
	return result, nil
}

func DecodeRecord(data []byte, maximum uint64) ([]byte, error) {
	value, n := binary.Uvarint(data)
	if n == 0 {
		return nil, fail("framing", "truncated unsigned varint")
	}
	if n < 0 {
		return nil, fail("framing", "unsigned varint overflows uint64")
	}
	if uint64(n) != UvarintSize(value) {
		return nil, fail("framing", "unsigned varint uses a non-minimal encoding")
	}
	prefixSize := uint64(n)
	if maximum < prefixSize || value > maximum-prefixSize {
		return nil, fail("frame_limit", "record exceeds max_frame_bytes")
	}
	recordSize := prefixSize + value
	if uint64(len(data)) < recordSize {
		return nil, fail("framing", "truncated length-delimited payload")
	}
	if uint64(len(data)) != recordSize {
		return nil, fail("framing", "trailing bytes after one record")
	}
	return data[n:], nil
}

func ValidateTranscript(t *runtimev1.ProtocolTranscript, liveFences ...*runtimev1.FencingContext) error {
	if t == nil {
		return fail("transcript", "transcript required")
	}
	if err := contract(t.GetContractVersion()); err != nil {
		return err
	}
	if err := text(t.GetName(), "transcript.name", 256); err != nil {
		return err
	}
	phase := "client_hello"
	var requested, limits *runtimev1.Limits
	var acceptedAt time.Time
	credits := uint32(0)
	var request *runtimev1.ExecutionRequest
	attempts := map[string]bool{}
	nextSequence := uint64(0)
	frames := map[uint64]*runtimev1.ExecutionFrame{}
	operations := map[string]*runtimev1.OriginOperationRef{}
	dispatched := map[string]bool{}
	dedupeRequired := map[string]bool{}
	terminalSeen, resumeRejected, cancelled := false, false, false
	needsResume, resumePending, replayingUnacknowledged := false, false, false
	acknowledgedSequence := int64(-1)
	currentAttemptID := ""
	errorCount, scrapeCount, browserCount, browserSuccessCount, batchCount, artifactCount := 0, 0, 0, 0, 0, 0
	var artifactBytes uint64
	artifactHandles := map[string]bool{}
	outputItems := uint64(0)
	monitorURLsSeen := map[string]bool{}
	monitorJobURLsSeen := map[string]bool{}
	for i, event := range t.GetEvents() {
		if event.GetDirection() == runtimev1.EventDirection_EVENT_DIRECTION_UNSPECIFIED || runtimev1.EventDirection_name[int32(event.GetDirection())] == "" {
			return fail("enum", fmt.Sprintf("events[%d] direction unspecified or unknown", i))
		}
		if terminalSeen || resumeRejected {
			return fail("terminal", "events after terminal/rejected resume")
		}
		var wireMessage proto.Message
		if event.GetClient() != nil {
			wireMessage = event.GetClient()
		} else if event.GetServer() != nil {
			wireMessage = event.GetServer()
		}
		if wireMessage != nil {
			ceiling := hardLimits.GetMaxFrameBytes()
			if limits != nil {
				ceiling = limits.GetMaxFrameBytes()
			}
			raw, _ := proto.Marshal(wireMessage)
			if uint64(len(raw))+UvarintSize(uint64(len(raw))) > ceiling {
				return fail("frame_limit", "length-delimited protocol record exceeds max_frame_bytes")
			}
		}
		switch item := event.GetEvent().(type) {
		case *runtimev1.ProtocolEvent_Fault:
			if event.GetDirection() != runtimev1.EventDirection_EVENT_DIRECTION_FAULT {
				return fail("direction", "fault direction mismatch")
			}
			fault := item.Fault
			if fault.GetPoint() == 0 || runtimev1.DisconnectPoint_name[int32(fault.GetPoint())] == "" || phase != "ready" || request == nil {
				return fail("disconnect", "invalid disconnect")
			}
			if fault.GetOriginRequestId() == "" {
				return fail("origin", "disconnect lacks operation ID")
			}
			if operations[fault.GetOriginRequestId()] == nil {
				return fail("origin", "disconnect references undeclared operation ID")
			}
			if fault.GetRequestFingerprint() != operations[fault.GetOriginRequestId()].GetRequestFingerprint() {
				return fail("fingerprint", "disconnect changed durable request fingerprint")
			}
			if fault.GetPoint() == runtimev1.DisconnectPoint_DISCONNECT_POINT_AFTER_DISPATCH && !fault.GetOriginWasDispatched() {
				return fail("disconnect", "AFTER_DISPATCH must record origin_was_dispatched=true")
			}
			if fault.GetOriginWasDispatched() {
				dispatched[fault.GetOriginRequestId()] = true
				if fault.GetPoint() == runtimev1.DisconnectPoint_DISCONNECT_POINT_AFTER_DISPATCH {
					dedupeRequired[fault.GetOriginRequestId()] = true
				}
			}
			if fault.GetPoint() == runtimev1.DisconnectPoint_DISCONNECT_POINT_AFTER_FRAME || fault.GetPoint() == runtimev1.DisconnectPoint_DISCONNECT_POINT_RESULT_BEFORE_TERMINAL {
				if fault.Sequence == nil || frames[fault.GetSequence()] == nil {
					return fail("disconnect", "fault sequence was not observed")
				}
			}
			phase = "client_hello"
			credits = 0
			needsResume = true
			resumePending = false
		case *runtimev1.ProtocolEvent_Client:
			if event.GetDirection() != runtimev1.EventDirection_EVENT_DIRECTION_CLIENT {
				return fail("direction", "client direction mismatch")
			}
			client := item.Client
			switch msg := client.GetPayload().(type) {
			case *runtimev1.ClientMessage_Hello:
				if phase != "client_hello" {
					return fail("handshake", "client hello out of order")
				}
				supported := false
				for _, v := range msg.Hello.GetSupportedContractVersions() {
					supported = supported || v == ContractVersion
				}
				if !supported || msg.Hello.GetImplementation() == 0 || runtimev1.Implementation_name[int32(msg.Hello.GetImplementation())] == "" {
					return fail("version", "client does not support v1")
				}
				if err := ValidateLimits(msg.Hello.GetRequestedLimits(), nil); err != nil {
					return err
				}
				requested = proto.Clone(msg.Hello.GetRequestedLimits()).(*runtimev1.Limits)
				phase = "server_hello"
			case *runtimev1.ClientMessage_Start:
				if phase != "ready" || request != nil {
					return fail("start", "start out of order/duplicate")
				}
				if err := ValidateRequest(msg.Start); err != nil {
					return err
				}
				deadline, _ := time.Parse(time.RFC3339Nano, msg.Start.GetDeadlineRfc3339())
				if !deadline.After(acceptedAt) || deadline.Sub(acceptedAt) > 15*time.Minute {
					return fail("deadline", "deadline must be after accepted_at and at most 15 minutes")
				}
				if msg.Start.GetKind() == runtimev1.ExecutionKind_EXECUTION_KIND_BROWSER {
					if err := ValidateBrowserPlan(msg.Start.GetBrowser().GetPlan(), limits); err != nil {
						return err
					}
				}
				request = proto.Clone(msg.Start).(*runtimev1.ExecutionRequest)
				if len(liveFences) > 0 {
					if err := ValidateFencing(liveFences[0], request.GetBoardManifest().GetConfigRevision()); err != nil {
						return err
					}
					if !bytes.Equal(deterministicBytes(liveFences[0]), deterministicBytes(request.GetFencingContext())) {
						return fail("fence", "request fencing context is stale against live caller")
					}
				}
				attempts[request.GetAttemptId()] = true
				currentAttemptID = request.GetAttemptId()
				var err error
				operations, err = ValidateOriginOperations(request.GetOriginOperations(), request.GetOriginRequestId())
				if err != nil {
					return err
				}
				nextSequence = 0
			case *runtimev1.ClientMessage_Resume:
				if phase != "ready" || request == nil || !needsResume {
					return fail("resume", "resume requires a disconnected prior execution")
				}
				resume := msg.Resume
				originalDeadline, _ := time.Parse(time.RFC3339Nano, request.GetDeadlineRfc3339())
				if !originalDeadline.After(acceptedAt) {
					return fail("deadline", "execution deadline expired before resume acceptance")
				}
				if err := contract(resume.GetContractVersion()); err != nil {
					return err
				}
				if resume.GetRequestId() != request.GetRequestId() || resume.GetOriginRequestId() != request.GetOriginRequestId() {
					return fail("resume", "semantic identity changed")
				}
				if attempts[resume.GetAttemptId()] || resume.GetAttemptId() == "" {
					return fail("resume", "attempt ID must be fresh")
				}
				attempts[resume.GetAttemptId()] = true
				if !bytes.Equal(deterministicBytes(resume.GetFencingContext()), deterministicBytes(request.GetFencingContext())) {
					return fail("fence", "resume changed the active fencing context")
				}
				currentAttemptID = resume.GetAttemptId()
				needsResume = false
				resumePending = true
				replayingUnacknowledged = true
				if resume.AfterSequence != nil {
					if frames[resume.GetAfterSequence()] == nil {
						return fail("resume", "after_sequence unseen")
					}
					if int64(resume.GetAfterSequence()) < acknowledgedSequence {
						return fail("resume", "after_sequence regressed acknowledged progress")
					}
					acknowledgedSequence = int64(resume.GetAfterSequence())
				}
				nextSequence = uint64(acknowledgedSequence + 1)
			case *runtimev1.ClientMessage_WindowUpdate:
				if phase != "ready" || limits == nil || request == nil || msg.WindowUpdate.GetRequestId() != request.GetRequestId() || msg.WindowUpdate.GetAdditionalFrames() == 0 {
					return fail("backpressure", "invalid window update")
				}
				if msg.WindowUpdate.GetAttemptId() != currentAttemptID || !bytes.Equal(msg.WindowUpdate.GetFenceDigest(), request.GetFencingContext().GetFenceDigest()) {
					return fail("fence", "window update used stale attempt/fencing identity")
				}
				if msg.WindowUpdate.GetAdditionalFrames() > limits.GetMaxInFlightFrames()-credits {
					return fail("backpressure", "credits exceed maximum")
				}
				credits += msg.WindowUpdate.GetAdditionalFrames()
			case *runtimev1.ClientMessage_Cancel:
				if phase != "ready" || request == nil || msg.Cancel.GetRequestId() != request.GetRequestId() || msg.Cancel.GetAttemptId() != currentAttemptID {
					return fail("cancel", "invalid cancel")
				}
				if !bytes.Equal(deterministicBytes(msg.Cancel.GetFencingContext()), deterministicBytes(request.GetFencingContext())) {
					return fail("fence", "cancel changed the active fencing context")
				}
				cancelled = true
			default:
				return fail("message", "untagged client message")
			}
		case *runtimev1.ProtocolEvent_Server:
			if event.GetDirection() != runtimev1.EventDirection_EVENT_DIRECTION_SERVER {
				return fail("direction", "server direction mismatch")
			}
			server := item.Server
			switch msg := server.GetPayload().(type) {
			case *runtimev1.ServerMessage_Hello:
				if phase != "server_hello" || requested == nil {
					return fail("handshake", "server hello out of order")
				}
				hello := msg.Hello
				if hello.GetSelectedContractVersion() != ContractVersion || !hello.GetResumeByOriginRequestId() || hello.GetImplementation() == 0 || runtimev1.Implementation_name[int32(hello.GetImplementation())] == "" {
					return fail("handshake", "server selection invalid")
				}
				if err := ValidateLimits(hello.GetAcceptedLimits(), requested); err != nil {
					return err
				}
				if !deadlineRE.MatchString(hello.GetAcceptedAtRfc3339()) || !strings.HasSuffix(hello.GetAcceptedAtRfc3339(), "Z") {
					return fail("deadline", "server accepted_at is not strict RFC3339")
				}
				var err error
				acceptedAt, err = time.Parse(time.RFC3339Nano, hello.GetAcceptedAtRfc3339())
				if err != nil {
					return fail("deadline", "server accepted_at is invalid")
				}
				if hello.GetInitialWindowFrames() == 0 || hello.GetInitialWindowFrames() > hello.GetAcceptedLimits().GetMaxInFlightFrames() {
					return fail("backpressure", "initial window invalid")
				}
				limits = proto.Clone(hello.GetAcceptedLimits()).(*runtimev1.Limits)
				credits = hello.GetInitialWindowFrames()
				phase = "ready"
			case *runtimev1.ServerMessage_ResumeRejected:
				if phase != "ready" || request == nil || !resumePending {
					return fail("resume", "resume rejection out of order")
				}
				rejected := msg.ResumeRejected
				if err := ValidateError(rejected.GetError(), limits); err != nil {
					return err
				}
				if rejected.GetRequestId() != request.GetRequestId() || rejected.GetOriginRequestId() != request.GetOriginRequestId() || rejected.GetError().GetCode() != runtimev1.ErrorCode_ERROR_CODE_AMBIGUOUS_ORIGIN {
					return fail("resume", "resume must fail closed")
				}
				if rejected.GetAttemptId() != currentAttemptID || !bytes.Equal(rejected.GetFenceDigest(), request.GetFencingContext().GetFenceDigest()) {
					return fail("fence", "resume rejection used stale attempt/fencing identity")
				}
				resumeRejected = true
			case *runtimev1.ServerMessage_Frame:
				if phase != "ready" || request == nil || limits == nil {
					return fail("frame", "frame out of order")
				}
				if credits == 0 {
					return fail("backpressure", "frame emitted without credit")
				}
				credits--
				frame := msg.Frame
				if err := contract(frame.GetContractVersion()); err != nil {
					return err
				}
				if frame.GetRequestId() != request.GetRequestId() || frame.GetSequence() != nextSequence {
					return fail("sequence", "frame ID/sequence invalid")
				}
				if frame.GetAttemptId() != currentAttemptID || !bytes.Equal(frame.GetFenceDigest(), request.GetFencingContext().GetFenceDigest()) {
					return fail("fence", "frame echoed stale/mismatched attempt or fencing digest")
				}
				raw, _ := proto.Marshal(frame)
				if uint64(len(raw))+UvarintSize(uint64(len(raw))) > limits.GetMaxFrameBytes() {
					return fail("frame_limit", "frame exceeds max_frame_bytes")
				}
				prior := frames[frame.GetSequence()]
				if prior == nil && uint32(len(frames)) >= limits.GetMaxExecutionFrames() {
					return fail("limit", "execution frame count exceeds negotiated limit")
				}
				if prior != nil {
					if !replayingUnacknowledged || int64(frame.GetSequence()) <= acknowledgedSequence || !bytes.Equal(semanticFrameBytes(prior), semanticFrameBytes(frame)) {
						return fail("sequence", "acknowledged or changed frame was replayed")
					}
					nextSequence++
					resumePending = false
					continue
				}
				replayingUnacknowledged = false
				resumePending = false
				frames[frame.GetSequence()] = frame
				nextSequence++
				if cancelled {
					if _, ok := frame.GetPayload().(*runtimev1.ExecutionFrame_Terminal); !ok {
						return fail("cancel", "non-terminal after cancel")
					}
				}
				if errorCount > 0 {
					if _, ok := frame.GetPayload().(*runtimev1.ExecutionFrame_Terminal); !ok {
						return fail("error_terminal", "non-terminal after error")
					}
				}
				if len(dedupeRequired) > 0 {
					contact, ok := frame.GetPayload().(*runtimev1.ExecutionFrame_OriginContact)
					if !ok || !dedupeRequired[contact.OriginContact.GetOperation().GetOriginRequestId()] || contact.OriginContact.GetDisposition() != runtimev1.OriginContactDisposition_ORIGIN_CONTACT_DISPOSITION_DEDUPLICATED {
						return fail("dedupe", "AFTER_DISPATCH resume requires a DEDUPLICATED origin contact")
					}
				}
				switch payload := frame.GetPayload().(type) {
				case *runtimev1.ExecutionFrame_OriginOperationDeclared:
					return fail("origin", "v1 disallows dynamic origin operation declarations")
				case *runtimev1.ExecutionFrame_OriginContact:
					op := payload.OriginContact.GetOperation()
					known := operations[op.GetOriginRequestId()]
					if known == nil {
						return fail("origin", "origin contact requires a durable pre-dispatch declaration")
					}
					if !bytes.Equal(deterministicBytes(known), deterministicBytes(op)) {
						return fail("origin", "operation identity changed")
					}
					if payload.OriginContact.GetRequestFingerprint() != known.GetRequestFingerprint() {
						return fail("fingerprint", "origin contact changed pre-dispatch request fingerprint")
					}
					switch payload.OriginContact.GetDisposition() {
					case runtimev1.OriginContactDisposition_ORIGIN_CONTACT_DISPOSITION_DISPATCHED:
						if dispatched[op.GetOriginRequestId()] {
							return fail("at_most_once", "origin dispatched twice")
						}
						dispatched[op.GetOriginRequestId()] = true
					case runtimev1.OriginContactDisposition_ORIGIN_CONTACT_DISPOSITION_DEDUPLICATED:
						if !dedupeRequired[op.GetOriginRequestId()] {
							return fail("dedupe", "deduplication is legal exactly once after ambiguous resume")
						}
						delete(dedupeRequired, op.GetOriginRequestId())
					default:
						return fail("enum", "origin disposition unspecified or unknown")
					}
					if payload.OriginContact.ExchangeArtifact != nil {
						if err := ValidateArtifact(payload.OriginContact.GetExchangeArtifact(), limits); err != nil {
							return err
						}
						artifact := payload.OriginContact.GetExchangeArtifact()
						if artifactHandles[artifact.GetHandle()] {
							return fail("duplicate", "artifact handle emitted more than once")
						}
						artifactHandles[artifact.GetHandle()] = true
						artifactCount++
						artifactBytes += artifact.GetSizeBytes()
					}
				case *runtimev1.ExecutionFrame_MonitorBatch:
					if request.GetKind() != runtimev1.ExecutionKind_EXECUTION_KIND_MONITOR {
						return fail("kind", "monitor batch on scrape")
					}
					if err := ValidateMonitorResult(payload.MonitorBatch, limits); err != nil {
						return err
					}
					for _, url := range payload.MonitorBatch.GetUrls() {
						if monitorURLsSeen[url] {
							return fail("duplicate", "monitor URLs must be unique across all batches")
						}
						monitorURLsSeen[url] = true
					}
					for _, job := range payload.MonitorBatch.GetJobs() {
						if monitorJobURLsSeen[job.GetUrl()] {
							return fail("duplicate", "monitor job URLs must be unique across all batches")
						}
						monitorJobURLsSeen[job.GetUrl()] = true
					}
					batchCount++
					outputItems += uint64(len(payload.MonitorBatch.GetUrls()))
					if uint32(batchCount) > limits.GetMaxMonitorBatches() {
						return fail("limit", "too many monitor batches")
					}
				case *runtimev1.ExecutionFrame_ScrapeResult:
					if request.GetKind() != runtimev1.ExecutionKind_EXECUTION_KIND_SCRAPE || scrapeCount > 0 {
						return fail("kind", "illegal scrape result")
					}
					if err := ValidateJobContent(payload.ScrapeResult.GetContent(), limits); err != nil {
						return err
					}
					scrapeCount++
					outputItems++
				case *runtimev1.ExecutionFrame_BrowserResult:
					if request.GetKind() != runtimev1.ExecutionKind_EXECUTION_KIND_BROWSER || browserCount > 0 {
						return fail("kind", "illegal browser result")
					}
					if err := ValidateBrowserResult(payload.BrowserResult, limits, request.GetBrowser().GetPlan()); err != nil {
						return err
					}
					for _, artifact := range BrowserArtifacts(payload.BrowserResult) {
						if artifactHandles[artifact.GetHandle()] {
							return fail("duplicate", "artifact handle emitted more than once")
						}
						artifactHandles[artifact.GetHandle()] = true
						artifactCount++
						artifactBytes += artifact.GetSizeBytes()
					}
					browserCount++
					if _, ok := payload.BrowserResult.GetOutcome().(*runtimev1.BrowserResult_Success); ok {
						browserSuccessCount++
						outputItems++
					} else {
						errorCount++
					}
				case *runtimev1.ExecutionFrame_Artifact:
					if err := ValidateArtifact(payload.Artifact.GetArtifact(), limits); err != nil {
						return err
					}
					if artifactHandles[payload.Artifact.GetArtifact().GetHandle()] {
						return fail("duplicate", "artifact handle emitted more than once")
					}
					artifactHandles[payload.Artifact.GetArtifact().GetHandle()] = true
					artifactCount++
					artifactBytes += payload.Artifact.GetArtifact().GetSizeBytes()
				case *runtimev1.ExecutionFrame_Error:
					if err := ValidateError(payload.Error, limits); err != nil {
						return err
					}
					errorCount++
					if errorCount > 1 || (request.GetKind() == runtimev1.ExecutionKind_EXECUTION_KIND_SCRAPE && scrapeCount > 0) || (request.GetKind() == runtimev1.ExecutionKind_EXECUTION_KIND_BROWSER && browserCount > 0) {
						return fail("error", "illegal result/error combination")
					}
				case *runtimev1.ExecutionFrame_Terminal:
					terminal := payload.Terminal
					if terminal.GetStatus() == 0 || runtimev1.TerminalStatus_name[int32(terminal.GetStatus())] == "" {
						return fail("enum", "terminal status unspecified or unknown")
					}
					if terminal.GetFrameCount() != uint64(len(frames)-1) || terminal.GetOutputItems() != outputItems || terminal.GetMonitorBatches() != uint32(batchCount) || terminal.GetArtifactCount() != uint32(artifactCount) || terminal.GetOriginOperationCount() != uint32(len(dispatched)) {
						return fail("count", "terminal counts disagree")
					}
					if outputItems > limits.GetMaxOutputItems() || terminal.GetActiveDurationMs() > limits.GetMaxActiveDurationMs() {
						return fail("limit", "terminal exceeds limits")
					}
					if uint32(artifactCount) > limits.GetMaxArtifactCount() || artifactBytes > limits.GetMaxArtifactTotalBytes() {
						return fail("artifact_limit", "execution artifacts exceed aggregate limits")
					}
					if cancelled && terminal.GetStatus() != runtimev1.TerminalStatus_TERMINAL_STATUS_CANCELLED {
						return fail("cancel", "cancellation requires a cancelled terminal")
					}
					switch terminal.GetStatus() {
					case runtimev1.TerminalStatus_TERMINAL_STATUS_SUCCESS:
						if errorCount > 0 || !terminal.GetEligibleForCommit() {
							return fail("terminal", "success must be commit-eligible/error-free")
						}
						if request.GetKind() == runtimev1.ExecutionKind_EXECUTION_KIND_MONITOR && batchCount == 0 {
							return fail("terminal", "monitor success needs batch")
						}
						if request.GetKind() == runtimev1.ExecutionKind_EXECUTION_KIND_SCRAPE && scrapeCount != 1 {
							return fail("terminal", "scrape success needs result")
						}
						if request.GetKind() == runtimev1.ExecutionKind_EXECUTION_KIND_BROWSER && browserSuccessCount != 1 {
							return fail("terminal", "browser success needs correlated result")
						}
						if len(dispatched) != len(operations) {
							return fail("terminal", "commit-eligible success requires every declared origin")
						}
					case runtimev1.TerminalStatus_TERMINAL_STATUS_ERROR:
						if errorCount != 1 || terminal.GetEligibleForCommit() {
							return fail("terminal", "error terminal invalid")
						}
					case runtimev1.TerminalStatus_TERMINAL_STATUS_CANCELLED:
						if !cancelled || terminal.GetEligibleForCommit() || errorCount > 0 {
							return fail("terminal", "cancel terminal invalid")
						}
					}
					terminalSeen = true
				default:
					return fail("frame", "untagged frame")
				}
			default:
				return fail("message", "untagged server message")
			}
		default:
			return fail("direction", "event payload missing")
		}
	}
	if request == nil {
		return fail("start", "transcript has no request")
	}
	if !terminalSeen && !resumeRejected {
		return fail("terminal", "incomplete without terminal")
	}
	return nil
}

func canonicalJSON(message proto.Message) ([]byte, error) {
	raw, err := (protojson.MarshalOptions{UseProtoNames: true}).Marshal(message)
	if err != nil {
		return nil, err
	}
	var value any
	if err := json.Unmarshal(raw, &value); err != nil {
		return nil, err
	}
	return json.Marshal(value)
}

func ContentHash(content *runtimev1.JobContent) string {
	canonical := proto.Clone(content).(*runtimev1.JobContent)
	sort.Slice(canonical.Localizations, func(i, j int) bool {
		left, right := canonical.Localizations[i], canonical.Localizations[j]
		if left.GetLocale() != right.GetLocale() {
			return left.GetLocale() < right.GetLocale()
		}
		if left.GetTitle() != right.GetTitle() {
			return left.GetTitle() < right.GetTitle()
		}
		return left.GetDescriptionHtml() < right.GetDescriptionHtml()
	})
	sort.Strings(canonical.Skills)
	sort.Slice(canonical.Extensions, func(i, j int) bool {
		return canonical.Extensions[i].GetSchemaId() < canonical.Extensions[j].GetSchemaId()
	})
	if canonical.Locations != nil {
		sort.Strings(canonical.Locations.Values)
	}
	raw := deterministicBytes(canonical)
	sum := sha256.Sum256(raw)
	return hex.EncodeToString(sum[:])
}

func ProjectFrames(frames []*runtimev1.ExecutionFrame, request *runtimev1.ExecutionRequest) (*runtimev1.ProjectedEffects, error) {
	result := &runtimev1.ProjectedEffects{GoneDetectionAllowed: true}
	urls := map[string]bool{}
	hashes := []string{}
	metadataDigest := sha256.New()
	metadataSeen := false
	jobEffects := []*runtimev1.JobEffect{}
	var metadataSize [8]byte
	for _, frame := range frames {
		switch payload := frame.GetPayload().(type) {
		case *runtimev1.ExecutionFrame_MonitorBatch:
			m := payload.MonitorBatch
			for _, u := range m.GetUrls() {
				urls[u] = true
			}
			for _, job := range m.GetJobs() {
				digest := ContentHash(job.GetContent())
				hashes = append(hashes, digest)
				jobEffects = append(jobEffects, &runtimev1.JobEffect{SourceUrl: job.GetUrl(), ContentSha256: digest})
			}
			result.GoneDetectionAllowed = result.GetGoneDetectionAllowed() && !m.GetTruncated()
			result.Hybrid = result.GetHybrid() || m.GetHybrid()
			result.Truncated = result.GetTruncated() || m.GetTruncated()
			result.FilteredCount += m.GetFilteredCount()
			result.SecurityFilteredCount += m.GetSecurityFilteredCount()
			if m.NewSitemapUrl != nil {
				v := m.GetNewSitemapUrl()
				result.NewSitemapUrl = &v
			}
			if m.MetadataUpdates != nil {
				raw := deterministicBytes(m.MetadataUpdates)
				binary.BigEndian.PutUint64(metadataSize[:], uint64(len(raw)))
				_, _ = metadataDigest.Write(metadataSize[:])
				_, _ = metadataDigest.Write(raw)
				metadataSeen = true
			}
		case *runtimev1.ExecutionFrame_ScrapeResult:
			digest := ContentHash(payload.ScrapeResult.GetContent())
			hashes = append(hashes, digest)
			if request == nil || request.GetKind() != runtimev1.ExecutionKind_EXECUTION_KIND_SCRAPE {
				return nil, fail("projection", "scrape projection requires its typed ExecutionRequest")
			}
			jobEffects = append(jobEffects, &runtimev1.JobEffect{SourceUrl: request.GetScrape().GetSourceUrl(), ContentSha256: digest})
		}
	}
	for u := range urls {
		result.UrlsToUpsert = append(result.UrlsToUpsert, u)
	}
	sort.Strings(result.UrlsToUpsert)
	sort.Strings(hashes)
	result.ContentHashes = hashes
	sort.Slice(jobEffects, func(i, j int) bool {
		if jobEffects[i].GetSourceUrl() != jobEffects[j].GetSourceUrl() {
			return jobEffects[i].GetSourceUrl() < jobEffects[j].GetSourceUrl()
		}
		return jobEffects[i].GetContentSha256() < jobEffects[j].GetContentSha256()
	})
	result.JobEffects = jobEffects
	if metadataSeen {
		v := hex.EncodeToString(metadataDigest.Sum(nil))
		result.MetadataUpdatesSha256 = &v
	}
	return result, nil
}

func SemanticHash(frames []*runtimev1.ExecutionFrame, projection *runtimev1.ProjectedEffects) (string, error) {
	digest := sha256.New()
	_, _ = digest.Write(append([]byte(ContractVersion), 0))
	messages := make([]proto.Message, 0, len(frames)+1)
	for _, frame := range frames {
		value := proto.Clone(frame).(*runtimev1.ExecutionFrame)
		value.AttemptId = ""
		messages = append(messages, value)
	}
	messages = append(messages, projection)
	var size [8]byte
	for _, message := range messages {
		raw := deterministicBytes(message)
		binary.BigEndian.PutUint64(size[:], uint64(len(raw)))
		_, _ = digest.Write(size[:])
		_, _ = digest.Write(raw)
	}
	return hex.EncodeToString(digest.Sum(nil)), nil
}

func ValidateReplay(replay *runtimev1.ReplayCase, limits *runtimev1.Limits) error {
	if replay == nil {
		return fail("replay", "replay required")
	}
	if limits == nil {
		limits = hardLimits
	}
	if err := contract(replay.GetContractVersion()); err != nil {
		return err
	}
	if err := text(replay.GetName(), "replay.name", 256); err != nil {
		return err
	}
	if err := text(replay.GetProviderFamily(), "replay.provider_family", 256); err != nil {
		return err
	}
	if err := hash(replay.GetExpectedSemanticSha256(), "replay.expected_semantic_sha256"); err != nil {
		return err
	}
	if err := ValidateRequest(replay.GetExecutionRequest()); err != nil {
		return err
	}
	if replay.GetAdapter() == 0 || runtimev1.ReplayAdapter_name[int32(replay.GetAdapter())] == "" {
		return fail("enum", "replay adapter unspecified or unknown")
	}
	if len(replay.GetExchanges()) == 0 {
		return fail("replay", "captured origin exchanges are required")
	}
	requestOperations := replay.GetExecutionRequest().GetOriginOperations()
	if len(replay.GetExchanges()) < len(requestOperations) {
		return fail("replay", "captured exchanges must cover initially declared operations")
	}
	ids := map[string]bool{}
	exchangeOperations := []*runtimev1.OriginOperationRef{}
	decoded := map[uint64]proto.Message{}
	for i, exchange := range replay.GetExchanges() {
		op := exchange.GetOperation()
		if i < len(requestOperations) && !bytes.Equal(deterministicBytes(op), deterministicBytes(requestOperations[i])) {
			return fail("origin", "captured exchange prefix differs from ExecutionRequest")
		}
		if op.GetOperationSequence() != uint32(i) {
			return fail("origin_sequence", "exchange order invalid")
		}
		if ids[op.GetOriginRequestId()] {
			return fail("duplicate", "duplicate exchange origin ID")
		}
		ids[op.GetOriginRequestId()] = true
		exchangeOperations = append(exchangeOperations, op)
		if !exchange.GetDeterministicallyRedacted() {
			return fail("redaction", "exchange is not redacted")
		}
		if err := text(exchange.GetRequest().GetMethod(), "replay.request.method", 16); err != nil {
			return err
		}
		if !httpMethodRE.MatchString(exchange.GetRequest().GetMethod()) {
			return fail("replay", "captured request method must be an uppercase HTTP token")
		}
		if err := validURL(exchange.GetRequest().GetUrl(), "replay.request.url"); err != nil {
			return err
		}
		if err := headers(exchange.GetRequest().GetHeaders(), "replay.request.headers"); err != nil {
			return err
		}
		if err := headers(exchange.GetResponse().GetHeaders(), "replay.response.headers"); err != nil {
			return err
		}
		if exchange.GetResponse().GetStatus() < 100 || exchange.GetResponse().GetStatus() > 599 {
			return fail("http_status", "captured response status must be in 100..599")
		}
		requestBody, err := ValidateChunkManifest(exchange.GetRequest().GetBody(), limits, limits.GetMaxHttpTransferBytes(), true)
		if err != nil {
			return err
		}
		responseBody, err := ValidateChunkManifest(exchange.GetResponse().GetBody(), limits, limits.GetMaxHttpTransferBytes(), true)
		if err != nil {
			return err
		}
		if err := validateRedactedBody(requestBody, "replay.request.body", 0); err != nil {
			return err
		}
		if err := validateRedactedBody(responseBody, "replay.response.body", 0); err != nil {
			return err
		}
		if RequestFingerprint(exchange.GetRequest()) != op.GetRequestFingerprint() {
			return fail("fingerprint", "captured request differs from durable operation fingerprint")
		}
		if exchange.NormalizedResultFrameSequence != nil {
			targetSequence := exchange.GetNormalizedResultFrameSequence()
			if decoded[targetSequence] != nil {
				return fail("replay", "normalized result frame mappings must be unique")
			}
			var message proto.Message
			if replay.GetAdapter() == runtimev1.ReplayAdapter_REPLAY_ADAPTER_NORMALIZED_MONITOR_JSON {
				message = &runtimev1.MonitorResult{}
			} else {
				message = &runtimev1.ScrapeResult{}
			}
			if err := (protojson.UnmarshalOptions{DiscardUnknown: false}).Unmarshal(responseBody, message); err != nil {
				return err
			}
			decoded[targetSequence] = message
		}
	}
	if _, err := ValidateOriginOperations(exchangeOperations, replay.GetExecutionRequest().GetOriginRequestId()); err != nil {
		return err
	}
	requested := proto.Clone(limits).(*runtimev1.Limits)
	accepted := proto.Clone(limits).(*runtimev1.Limits)
	if accepted.GetMaxInFlightFrames() > 64 {
		accepted.MaxInFlightFrames = 64
	}
	events := []*runtimev1.ProtocolEvent{
		{
			Direction: runtimev1.EventDirection_EVENT_DIRECTION_CLIENT,
			Event: &runtimev1.ProtocolEvent_Client{Client: &runtimev1.ClientMessage{Payload: &runtimev1.ClientMessage_Hello{Hello: &runtimev1.ClientHello{
				SupportedContractVersions: []string{ContractVersion}, Implementation: runtimev1.Implementation_IMPLEMENTATION_PYTHON, RequestedLimits: requested,
			}}}},
		},
		{
			Direction: runtimev1.EventDirection_EVENT_DIRECTION_SERVER,
			Event: &runtimev1.ProtocolEvent_Server{Server: &runtimev1.ServerMessage{Payload: &runtimev1.ServerMessage_Hello{Hello: &runtimev1.ServerHello{
				SelectedContractVersion: ContractVersion, Implementation: runtimev1.Implementation_IMPLEMENTATION_GO, AcceptedLimits: accepted,
				InitialWindowFrames: accepted.GetMaxInFlightFrames(), ResumeByOriginRequestId: true, AcceptedAtRfc3339: "2026-08-26T11:50:00Z",
			}}}},
		},
		{
			Direction: runtimev1.EventDirection_EVENT_DIRECTION_CLIENT,
			Event:     &runtimev1.ProtocolEvent_Client{Client: &runtimev1.ClientMessage{Payload: &runtimev1.ClientMessage_Start{Start: replay.GetExecutionRequest()}}},
		},
	}
	credits := accepted.GetMaxInFlightFrames()
	for _, frame := range replay.GetExpectedFrames() {
		if credits == 0 {
			events = append(events, &runtimev1.ProtocolEvent{
				Direction: runtimev1.EventDirection_EVENT_DIRECTION_CLIENT,
				Event: &runtimev1.ProtocolEvent_Client{Client: &runtimev1.ClientMessage{Payload: &runtimev1.ClientMessage_WindowUpdate{WindowUpdate: &runtimev1.WindowUpdate{
					RequestId: replay.GetExecutionRequest().GetRequestId(), AdditionalFrames: accepted.GetMaxInFlightFrames(),
					AttemptId: replay.GetExecutionRequest().GetAttemptId(), FenceDigest: replay.GetExecutionRequest().GetFencingContext().GetFenceDigest(),
				}}}},
			})
			credits = accepted.GetMaxInFlightFrames()
		}
		events = append(events, &runtimev1.ProtocolEvent{
			Direction: runtimev1.EventDirection_EVENT_DIRECTION_SERVER,
			Event:     &runtimev1.ProtocolEvent_Server{Server: &runtimev1.ServerMessage{Payload: &runtimev1.ServerMessage_Frame{Frame: frame}}},
		})
		credits--
	}
	if err := ValidateTranscript(&runtimev1.ProtocolTranscript{ContractVersion: ContractVersion, Name: "replay:" + replay.GetName(), Events: events}); err != nil {
		return err
	}
	contactOperations := []*runtimev1.OriginOperationRef{}
	for _, frame := range replay.GetExpectedFrames() {
		if frame.GetOriginContact() != nil {
			contactOperations = append(contactOperations, frame.GetOriginContact().GetOperation())
		}
	}
	if len(contactOperations) != len(replay.GetExchanges()) {
		return fail("origin", "expected origin-contact frames must exactly match captured exchanges")
	}
	for i, op := range contactOperations {
		if !bytes.Equal(deterministicBytes(op), deterministicBytes(replay.GetExchanges()[i].GetOperation())) {
			return fail("origin", "expected origin-contact frames must exactly match captured exchanges")
		}
	}
	resultFrames := map[uint64]*runtimev1.ExecutionFrame{}
	for _, frame := range replay.GetExpectedFrames() {
		switch frame.GetPayload().(type) {
		case *runtimev1.ExecutionFrame_MonitorBatch, *runtimev1.ExecutionFrame_ScrapeResult:
			resultFrames[frame.GetSequence()] = frame
		}
	}
	if len(resultFrames) != len(decoded) {
		return fail("replay", "normalized response mappings must exactly cover result frames")
	}
	for sequence, message := range decoded {
		frame := resultFrames[sequence]
		if frame == nil {
			return fail("replay", "normalized response mapping references a non-result frame")
		}
		var expected proto.Message
		if _, ok := message.(*runtimev1.MonitorResult); ok {
			if frame.GetMonitorBatch() == nil {
				return fail("replay", "monitor adapter mapped to a non-monitor frame")
			}
			expected = frame.GetMonitorBatch()
		} else {
			if frame.GetScrapeResult() == nil {
				return fail("replay", "scrape adapter mapped to a non-scrape frame")
			}
			expected = frame.GetScrapeResult()
		}
		left, _ := canonicalJSON(message)
		right, _ := canonicalJSON(expected)
		if !bytes.Equal(left, right) {
			return fail("replay", "decoded result differs")
		}
	}
	projection, err := ProjectFrames(replay.GetExpectedFrames(), replay.GetExecutionRequest())
	if err != nil {
		return err
	}
	left, _ := canonicalJSON(projection)
	right, _ := canonicalJSON(replay.GetExpectedProjection())
	if !bytes.Equal(left, right) {
		return fail("projection", fmt.Sprintf("projected effects differ: got %s want %s", left, right))
	}
	semantic, err := SemanticHash(replay.GetExpectedFrames(), projection)
	if err != nil {
		return err
	}
	if semantic != replay.GetExpectedSemanticSha256() {
		return fail("hash", "semantic hash mismatch")
	}
	return nil
}

func ValidateCase(c *runtimev1.ConformanceCase) error {
	if c == nil {
		return fail("case", "case required")
	}
	switch subject := c.GetSubject().(type) {
	case *runtimev1.ConformanceCase_Transcript:
		return ValidateTranscript(subject.Transcript)
	case *runtimev1.ConformanceCase_BrowserPlan:
		return ValidateBrowserPlan(subject.BrowserPlan, nil)
	case *runtimev1.ConformanceCase_BrowserResult:
		return ValidateBrowserResult(subject.BrowserResult, nil)
	case *runtimev1.ConformanceCase_Replay:
		return ValidateReplay(subject.Replay, nil)
	default:
		return fail("case", "case has no tagged subject")
	}
}
