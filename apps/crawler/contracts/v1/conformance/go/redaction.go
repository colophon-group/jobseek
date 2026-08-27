package adjacentpolicy

// Candidate-only offline privacy conformance. This file has no runtime,
// persistence, artifact-resolution, or network authority.

import (
	"bytes"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"unicode/utf8"
)

const privacyResultDomain = "jobseek.runtime.v1.redaction.result\x00"

var emailScalarPattern = regexp.MustCompile(`[A-Za-z0-9.!#$%&'*+/=?^_` + "`" + `{|}~-]+@[A-Za-z0-9][A-Za-z0-9-]*(?:\.[A-Za-z0-9][A-Za-z0-9-]*)+`)

type privacyFailure struct{ code string }

func (failure privacyFailure) Error() string { return failure.code }

func privacyFail(code string) error { return privacyFailure{code: code} }

func privacyFailureCode(err error, registry privacyRegistry) string {
	var failure privacyFailure
	if errors.As(err, &failure) && contains(registry.RejectedCodes, failure.code) {
		return failure.code
	}
	return "malformed_encoding"
}

type privacyLimits struct {
	MaxChunks            int `json:"max_chunks"`
	MaxDecodedWorkingSet int `json:"max_decoded_working_set_bytes"`
	MaxEncodedInput      int `json:"max_encoded_input_bytes"`
	MaxJSONDepth         int `json:"max_json_depth"`
	MaxOutput            int `json:"max_output_bytes"`
	MaxStructuredItems   int `json:"max_structured_items"`
}

type privacyRule struct {
	Contexts []string `json:"contexts"`
	ID       string   `json:"id"`
	Keys     []string `json:"keys"`
	Kind     string   `json:"kind"`
	Prefixes []string `json:"prefixes"`
}

type privacyRegistry struct {
	Contexts           []string         `json:"contexts"`
	ExtensionEnvelopes []map[string]any `json:"extension_envelopes"`
	Format             string           `json:"format"`
	KeyNormalization   map[string]any   `json:"key_normalization"`
	Limits             privacyLimits    `json:"limits"`
	RejectedCodes      []string         `json:"rejected_codes"`
	Rules              []privacyRule    `json:"rules"`
	Statuses           []string         `json:"statuses"`
	Wrappers           []string         `json:"wrappers"`
}

type privacyCorpus struct {
	Cases              []privacyCase       `json:"cases"`
	Format             string              `json:"format"`
	HelperLimitVectors []helperLimitVector `json:"helper_limit_vectors"`
	RequiredCaseIDs    []string            `json:"required_case_ids"`
}

type helperLimitVector struct {
	Accepted bool   `json:"accepted"`
	ID       string `json:"id"`
	Limit    int    `json:"limit"`
	Observed int    `json:"observed"`
}

type privacyCase struct {
	CaseID       string                     `json:"case_id"`
	Context      string                     `json:"context"`
	Expected     map[string]any             `json:"expected"`
	Input        map[string]json.RawMessage `json:"input"`
	ResultDigest string                     `json:"result_digest"`
	Wrapper      string                     `json:"wrapper"`
}

type privacyFinding struct {
	Context string `json:"context"`
	RuleID  string `json:"rule_id"`
}

type privacyResult struct {
	Status    string
	Output    []byte
	Findings  []privacyFinding
	ErrorCode string
}

type privacyValidator struct {
	registry       privacyRegistry
	credentialKeys map[string]bool
	cookieKeys     map[string]bool
	secretKeys     map[string]bool
}

func contains(values []string, target string) bool {
	for _, value := range values {
		if value == target {
			return true
		}
	}
	return false
}

func readStrictJSON(path string, target any) error {
	data, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.UseNumber()
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(target); err != nil {
		return err
	}
	var trailing any
	if err := decoder.Decode(&trailing); err != io.EOF {
		return fmt.Errorf("trailing JSON")
	}
	return nil
}

func loadPrivacyAssets(root string) (privacyValidator, privacyCorpus, error) {
	var registry privacyRegistry
	if err := readStrictJSON(filepath.Join(root, "privacy_registry.json"), &registry); err != nil {
		return privacyValidator{}, privacyCorpus{}, err
	}
	var corpus privacyCorpus
	if err := readStrictJSON(filepath.Join(root, "fixtures", "redaction", "manifest.json"), &corpus); err != nil {
		return privacyValidator{}, privacyCorpus{}, err
	}
	validator := privacyValidator{
		registry:       registry,
		credentialKeys: map[string]bool{},
		cookieKeys:     map[string]bool{},
		secretKeys:     map[string]bool{},
	}
	for _, rule := range registry.Rules {
		var destination map[string]bool
		switch rule.ID {
		case "credential_header":
			destination = validator.credentialKeys
		case "cookie":
			destination = validator.cookieKeys
		case "secret_key":
			destination = validator.secretKeys
		default:
			continue
		}
		for _, key := range rule.Keys {
			destination[key] = true
		}
	}
	return validator, corpus, nil
}

func canonicalJSON(value any) ([]byte, error) {
	var intermediate bytes.Buffer
	encoder := json.NewEncoder(&intermediate)
	encoder.SetEscapeHTML(false)
	if err := encoder.Encode(value); err != nil {
		return nil, err
	}
	decoder := json.NewDecoder(&intermediate)
	decoder.UseNumber()
	var normalized any
	if err := decoder.Decode(&normalized); err != nil {
		return nil, err
	}
	var output bytes.Buffer
	if err := appendCanonicalJSON(&output, normalized); err != nil {
		return nil, err
	}
	return output.Bytes(), nil
}

func appendCanonicalJSONString(output *bytes.Buffer, value string) error {
	if !utf8.ValidString(value) {
		return fmt.Errorf("invalid UTF-8 string")
	}
	const hexadecimal = "0123456789abcdef"
	output.WriteByte('"')
	for _, character := range value {
		switch character {
		case '"', '\\':
			output.WriteByte('\\')
			output.WriteRune(character)
		case '\b':
			output.WriteString("\\b")
		case '\t':
			output.WriteString("\\t")
		case '\n':
			output.WriteString("\\n")
		case '\f':
			output.WriteString("\\f")
		case '\r':
			output.WriteString("\\r")
		default:
			if character < 0x20 {
				output.WriteString("\\u00")
				output.WriteByte(hexadecimal[byte(character)>>4])
				output.WriteByte(hexadecimal[byte(character)&15])
			} else {
				output.WriteRune(character)
			}
		}
	}
	output.WriteByte('"')
	return nil
}

func appendCanonicalJSON(output *bytes.Buffer, value any) error {
	switch typed := value.(type) {
	case nil:
		output.WriteString("null")
	case bool:
		if typed {
			output.WriteString("true")
		} else {
			output.WriteString("false")
		}
	case json.Number:
		output.WriteString(typed.String())
	case string:
		return appendCanonicalJSONString(output, typed)
	case []any:
		output.WriteByte('[')
		for index, item := range typed {
			if index > 0 {
				output.WriteByte(',')
			}
			if err := appendCanonicalJSON(output, item); err != nil {
				return err
			}
		}
		output.WriteByte(']')
	case map[string]any:
		keys := make([]string, 0, len(typed))
		for key := range typed {
			keys = append(keys, key)
		}
		sort.Strings(keys)
		output.WriteByte('{')
		for index, key := range keys {
			if index > 0 {
				output.WriteByte(',')
			}
			if err := appendCanonicalJSONString(output, key); err != nil {
				return err
			}
			output.WriteByte(':')
			if err := appendCanonicalJSON(output, typed[key]); err != nil {
				return err
			}
		}
		output.WriteByte('}')
	default:
		return fmt.Errorf("unsupported canonical JSON type %T", value)
	}
	return nil
}

func bounded(observed int, maximum int) error {
	if observed > maximum {
		return privacyFail("limit_exceeded")
	}
	return nil
}

func strictBase64(value string, code string) ([]byte, error) {
	decoded, err := base64.StdEncoding.Strict().DecodeString(value)
	if err != nil {
		return nil, privacyFail(code)
	}
	return decoded, nil
}

type chunkManifest struct {
	Chunks    []map[string]json.RawMessage `json:"chunks"`
	Complete  bool                         `json:"complete"`
	TotalSize int                          `json:"total_size"`
}

type repeatASCII struct {
	Count         int    `json:"count"`
	PrefixB64     string `json:"prefix_b64"`
	RepeatByteB64 string `json:"repeat_byte_b64"`
	SuffixB64     string `json:"suffix_b64"`
}

func (validator privacyValidator) loadInput(source map[string]json.RawMessage) ([]byte, error) {
	if len(source) != 1 {
		return nil, privacyFail("malformed_encoding")
	}
	if encoded, ok := source["inline_b64"]; ok {
		var value string
		if err := json.Unmarshal(encoded, &value); err != nil {
			return nil, privacyFail("malformed_encoding")
		}
		raw, err := strictBase64(value, "malformed_encoding")
		if err != nil {
			return nil, err
		}
		if err := bounded(len(raw), validator.registry.Limits.MaxEncodedInput); err != nil {
			return nil, err
		}
		return raw, nil
	}
	if encoded, ok := source["repeat_ascii"]; ok {
		var descriptor repeatASCII
		if err := json.Unmarshal(encoded, &descriptor); err != nil || descriptor.Count < 0 {
			return nil, privacyFail("malformed_encoding")
		}
		prefix, err := strictBase64(descriptor.PrefixB64, "malformed_encoding")
		if err != nil {
			return nil, err
		}
		repeated, err := strictBase64(descriptor.RepeatByteB64, "malformed_encoding")
		if err != nil || len(repeated) != 1 || repeated[0] > 0x7f {
			return nil, privacyFail("malformed_encoding")
		}
		suffix, err := strictBase64(descriptor.SuffixB64, "malformed_encoding")
		if err != nil {
			return nil, err
		}
		total := len(prefix) + descriptor.Count + len(suffix)
		if err := bounded(total, validator.registry.Limits.MaxEncodedInput); err != nil {
			return nil, err
		}
		output := make([]byte, 0, total)
		output = append(output, prefix...)
		output = append(output, bytes.Repeat(repeated, descriptor.Count)...)
		output = append(output, suffix...)
		return output, nil
	}
	encoded, ok := source["chunk_manifest"]
	if !ok {
		return nil, privacyFail("malformed_encoding")
	}
	var manifest chunkManifest
	if err := json.Unmarshal(encoded, &manifest); err != nil {
		return nil, privacyFail("invalid_chunks")
	}
	if err := bounded(len(manifest.Chunks), validator.registry.Limits.MaxChunks); err != nil {
		return nil, err
	}
	if manifest.TotalSize < 0 {
		return nil, privacyFail("invalid_chunks")
	}
	if err := bounded(manifest.TotalSize, validator.registry.Limits.MaxEncodedInput); err != nil {
		return nil, err
	}
	if !manifest.Complete {
		return nil, privacyFail("invalid_chunks")
	}
	parts := make([][]byte, 0, len(manifest.Chunks))
	total := 0
	for expectedSequence, chunk := range manifest.Chunks {
		var sequence int
		var size int
		var digest string
		if err := json.Unmarshal(chunk["sequence"], &sequence); err != nil || sequence != expectedSequence {
			return nil, privacyFail("invalid_chunks")
		}
		if err := json.Unmarshal(chunk["size"], &size); err != nil || size < 0 {
			return nil, privacyFail("invalid_chunks")
		}
		if err := json.Unmarshal(chunk["sha256"], &digest); err != nil || len(digest) != 64 {
			return nil, privacyFail("invalid_chunks")
		}
		if _, err := hex.DecodeString(digest); err != nil || strings.ToLower(digest) != digest {
			return nil, privacyFail("invalid_chunks")
		}
		if _, exists := chunk["artifact"]; exists {
			return nil, privacyFail("artifact_unavailable")
		}
		var encodedPart string
		if err := json.Unmarshal(chunk["data_b64"], &encodedPart); err != nil {
			return nil, privacyFail("invalid_chunks")
		}
		part, err := strictBase64(encodedPart, "invalid_chunks")
		if err != nil || len(part) != size {
			return nil, privacyFail("invalid_chunks")
		}
		digestBytes := sha256.Sum256(part)
		if hex.EncodeToString(digestBytes[:]) != digest {
			return nil, privacyFail("invalid_chunks")
		}
		total += size
		if total > manifest.TotalSize {
			return nil, privacyFail("invalid_chunks")
		}
		parts = append(parts, part)
	}
	if total != manifest.TotalSize {
		return nil, privacyFail("invalid_chunks")
	}
	return bytes.Join(parts, nil), nil
}

func hexNibble(value byte) (byte, bool) {
	switch {
	case value >= '0' && value <= '9':
		return value - '0', true
	case value >= 'A' && value <= 'F':
		return value - 'A' + 10, true
	case value >= 'a' && value <= 'f':
		return value - 'a' + 10, true
	default:
		return 0, false
	}
}

func decodePercent(raw []byte) ([]byte, error) {
	output := make([]byte, 0, len(raw))
	for offset := 0; offset < len(raw); offset++ {
		if raw[offset] > 0x7f {
			return nil, privacyFail("malformed_encoding")
		}
		if raw[offset] != '%' {
			output = append(output, raw[offset])
			continue
		}
		if offset+2 >= len(raw) {
			return nil, privacyFail("malformed_encoding")
		}
		high, highOK := hexNibble(raw[offset+1])
		low, lowOK := hexNibble(raw[offset+2])
		if !highOK || !lowOK {
			return nil, privacyFail("malformed_encoding")
		}
		output = append(output, high<<4|low)
		offset += 2
	}
	return output, nil
}

func percentEncode(raw []byte) []byte {
	const digits = "0123456789ABCDEF"
	output := make([]byte, 0, len(raw)*3)
	for _, value := range raw {
		if value >= 'A' && value <= 'Z' || value >= 'a' && value <= 'z' ||
			value >= '0' && value <= '9' || strings.ContainsRune("-_.~", rune(value)) {
			output = append(output, value)
			continue
		}
		output = append(output, '%', digits[value>>4], digits[value&15])
	}
	return output
}

func (validator privacyValidator) decodeWrapper(raw []byte, wrapper string) ([]byte, error) {
	if wrapper == "" {
		return raw, nil
	}
	if !contains(validator.registry.Wrappers, wrapper) {
		return nil, privacyFail("unsupported_envelope")
	}
	var decoded []byte
	var err error
	if wrapper == "percent" {
		decoded, err = decodePercent(raw)
	} else {
		decoded, err = strictBase64(string(raw), "malformed_encoding")
	}
	if err != nil {
		return nil, err
	}
	if err := bounded(len(decoded), validator.registry.Limits.MaxDecodedWorkingSet); err != nil {
		return nil, err
	}
	return decoded, nil
}

func encodeWrapper(raw []byte, wrapper string) []byte {
	switch wrapper {
	case "percent":
		return percentEncode(raw)
	case "base64":
		return []byte(base64.StdEncoding.EncodeToString(raw))
	default:
		return raw
	}
}

func normalizePrivacyKey(value string) string {
	var output strings.Builder
	separator := false
	for _, character := range value {
		if character >= 'A' && character <= 'Z' {
			character += 'a' - 'A'
		}
		if strings.ContainsRune("-_. \t\n\v\f\r", character) {
			if !separator {
				output.WriteByte('-')
			}
			separator = true
			continue
		}
		output.WriteRune(character)
		separator = false
	}
	return output.String()
}

func (validator privacyValidator) keyRule(key string, context string) string {
	normalized := normalizePrivacyKey(key)
	if context == "headers" && validator.credentialKeys[normalized] {
		return "credential_header"
	}
	if validator.cookieKeys[normalized] {
		return "cookie"
	}
	if validator.secretKeys[normalized] {
		return "secret_key"
	}
	return ""
}

func scalarRule(value string) string {
	lowered := strings.ToLower(value)
	if strings.HasPrefix(lowered, "basic ") || strings.HasPrefix(lowered, "bearer ") {
		return "credential_scheme"
	}
	if emailScalarPattern.MatchString(value) {
		return "email"
	}
	return ""
}

func replacement(ruleID string) string { return "[REDACTED:" + ruleID + "]" }

func finding(ruleID string, context string) privacyFinding {
	return privacyFinding{Context: context, RuleID: ruleID}
}

func validUTF8(raw []byte) (string, error) {
	if !utf8.Valid(raw) {
		return "", privacyFail("malformed_encoding")
	}
	return string(raw), nil
}

func (validator privacyValidator) redactHeaders(raw []byte, findingContext string) ([]byte, []privacyFinding, int, error) {
	text, err := validUTF8(raw)
	if err != nil {
		return nil, nil, 0, err
	}
	if !strings.HasSuffix(text, "\n") || strings.ContainsAny(text, "\r\x00") {
		return nil, nil, 0, privacyFail("malformed_encoding")
	}
	lines := strings.Split(strings.TrimSuffix(text, "\n"), "\n")
	if err := bounded(len(lines), validator.registry.Limits.MaxStructuredItems); err != nil {
		return nil, nil, 0, err
	}
	var output strings.Builder
	findings := []privacyFinding{}
	for _, line := range lines {
		if !strings.Contains(line, ": ") {
			return nil, nil, 0, privacyFail("malformed_encoding")
		}
		name, value, _ := strings.Cut(line, ": ")
		if name == "" {
			return nil, nil, 0, privacyFail("malformed_encoding")
		}
		for _, character := range name {
			if character < 0x20 || character > 0x7e || character == ':' {
				return nil, nil, 0, privacyFail("malformed_encoding")
			}
		}
		ruleID := validator.keyRule(name, "headers")
		if ruleID == "" {
			ruleID = scalarRule(value)
		}
		if ruleID != "" {
			value = replacement(ruleID)
			findings = append(findings, finding(ruleID, findingContext))
		}
		output.WriteString(name + ": " + value + "\n")
	}
	return []byte(output.String()), findings, len(lines), nil
}

func strictURLDecode(value string) (string, error) {
	decoded, err := decodePercent([]byte(value))
	if err != nil {
		return "", err
	}
	return validUTF8(decoded)
}

func (validator privacyValidator) redactURL(raw []byte, findingContext string) ([]byte, []privacyFinding, int, error) {
	text, err := validUTF8(raw)
	if err != nil || strings.IndexFunc(text, func(r rune) bool { return r < 0x20 }) >= 0 {
		return nil, nil, 0, privacyFail("malformed_encoding")
	}
	parsed, err := url.Parse(text)
	if err != nil || (parsed.Scheme != "http" && parsed.Scheme != "https") || parsed.Hostname() == "" || parsed.Fragment != "" {
		return nil, nil, 0, privacyFail("malformed_encoding")
	}
	findings := []privacyFinding{}
	if parsed.User != nil {
		if _, err := strictURLDecode(parsed.User.String()); err != nil {
			return nil, nil, 0, err
		}
		parsed.User = url.User(replacement("url_userinfo"))
		findings = append(findings, finding("url_userinfo", findingContext))
	}
	encodedPairs := []string{}
	if parsed.RawQuery != "" {
		for _, field := range strings.Split(parsed.RawQuery, "&") {
			if strings.Count(field, "=") != 1 {
				return nil, nil, 0, privacyFail("malformed_encoding")
			}
			key, value, _ := strings.Cut(field, "=")
			decodedKey, err := strictURLDecode(key)
			if err != nil {
				return nil, nil, 0, err
			}
			decodedValue, err := strictURLDecode(value)
			if err != nil {
				return nil, nil, 0, err
			}
			ruleID := validator.keyRule(decodedKey, "url")
			if ruleID == "" {
				ruleID = scalarRule(decodedValue)
			}
			if ruleID != "" {
				decodedValue = replacement(ruleID)
				findings = append(findings, finding(ruleID, findingContext))
			}
			encodedPairs = append(encodedPairs, string(percentEncode([]byte(decodedKey)))+"="+string(percentEncode([]byte(decodedValue))))
		}
	}
	if err := bounded(len(encodedPairs), validator.registry.Limits.MaxStructuredItems); err != nil {
		return nil, nil, 0, err
	}
	parsed.RawQuery = strings.Join(encodedPairs, "&")
	return []byte(parsed.String()), findings, len(encodedPairs), nil
}

type jsonScanFrame struct {
	opening byte
	keys    map[string]bool
}

func scanUnicodeEscape(raw []byte, offset int) (uint16, bool) {
	if offset+4 > len(raw) {
		return 0, false
	}
	var value uint16
	for _, character := range raw[offset : offset+4] {
		nibble, ok := hexNibble(character)
		if !ok {
			return 0, false
		}
		value = value<<4 | uint16(nibble)
	}
	return value, true
}

func scanJSONString(raw []byte, start int) (int, error) {
	for offset := start + 1; offset < len(raw); {
		character := raw[offset]
		if character == '"' {
			return offset + 1, nil
		}
		if character < 0x20 {
			return 0, privacyFail("malformed_encoding")
		}
		if character != '\\' {
			_, size := utf8.DecodeRune(raw[offset:])
			if size == 0 {
				return 0, privacyFail("malformed_encoding")
			}
			offset += size
			continue
		}
		if offset+1 >= len(raw) {
			return 0, privacyFail("malformed_encoding")
		}
		escaped := raw[offset+1]
		if strings.ContainsRune("\"\\/bfnrt", rune(escaped)) {
			offset += 2
			continue
		}
		if escaped != 'u' {
			return 0, privacyFail("malformed_encoding")
		}
		codepoint, ok := scanUnicodeEscape(raw, offset+2)
		if !ok {
			return 0, privacyFail("malformed_encoding")
		}
		offset += 6
		if codepoint >= 0xd800 && codepoint <= 0xdbff {
			if offset+6 > len(raw) || raw[offset] != '\\' || raw[offset+1] != 'u' {
				return 0, privacyFail("malformed_encoding")
			}
			low, ok := scanUnicodeEscape(raw, offset+2)
			if !ok || low < 0xdc00 || low > 0xdfff {
				return 0, privacyFail("malformed_encoding")
			}
			offset += 6
		} else if codepoint >= 0xdc00 && codepoint <= 0xdfff {
			return 0, privacyFail("malformed_encoding")
		}
	}
	return 0, privacyFail("malformed_encoding")
}

func preflightJSON(raw []byte, maximumDepth int) error {
	if !utf8.Valid(raw) {
		return privacyFail("malformed_encoding")
	}
	stack := []jsonScanFrame{}
	for offset := 0; offset < len(raw); {
		switch raw[offset] {
		case '{', '[':
			frame := jsonScanFrame{opening: raw[offset]}
			if raw[offset] == '{' {
				frame.keys = map[string]bool{}
			}
			stack = append(stack, frame)
			if len(stack) > maximumDepth {
				return privacyFail("limit_exceeded")
			}
			offset++
		case '}', ']':
			expected := byte('{')
			if raw[offset] == ']' {
				expected = '['
			}
			if len(stack) == 0 || stack[len(stack)-1].opening != expected {
				return privacyFail("malformed_encoding")
			}
			stack = stack[:len(stack)-1]
			offset++
		case '"':
			end, err := scanJSONString(raw, offset)
			if err != nil {
				return err
			}
			lookahead := end
			for lookahead < len(raw) && strings.ContainsRune(" \t\r\n", rune(raw[lookahead])) {
				lookahead++
			}
			if lookahead < len(raw) && raw[lookahead] == ':' {
				if len(stack) == 0 || stack[len(stack)-1].opening != '{' {
					return privacyFail("malformed_encoding")
				}
				var key string
				if err := json.Unmarshal(raw[offset:end], &key); err != nil {
					return privacyFail("malformed_encoding")
				}
				keys := stack[len(stack)-1].keys
				if keys[key] {
					return privacyFail("malformed_encoding")
				}
				keys[key] = true
			}
			offset = end
		default:
			offset++
		}
	}
	if len(stack) != 0 {
		return privacyFail("malformed_encoding")
	}
	return nil
}

func decodeJSONValue(raw []byte, maximumDepth int) (any, error) {
	if err := preflightJSON(raw, maximumDepth); err != nil {
		return nil, err
	}
	if !utf8.Valid(raw) {
		return nil, privacyFail("malformed_encoding")
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	var value any
	if err := decoder.Decode(&value); err != nil {
		return nil, privacyFail("malformed_encoding")
	}
	var trailing any
	if err := decoder.Decode(&trailing); err != io.EOF {
		return nil, privacyFail("malformed_encoding")
	}
	return value, nil
}

func jsonMetrics(value any, depth int) (int, int) {
	maximumDepth := depth
	nodes := 1
	switch typed := value.(type) {
	case map[string]any:
		for _, child := range typed {
			childDepth, childNodes := jsonMetrics(child, depth+1)
			if childDepth > maximumDepth {
				maximumDepth = childDepth
			}
			nodes += childNodes
		}
	case []any:
		for _, child := range typed {
			childDepth, childNodes := jsonMetrics(child, depth+1)
			if childDepth > maximumDepth {
				maximumDepth = childDepth
			}
			nodes += childNodes
		}
	}
	return maximumDepth, nodes
}

func (validator privacyValidator) redactJSONValue(value any, findingContext string, forcedRule string) (any, []privacyFinding) {
	switch typed := value.(type) {
	case map[string]any:
		output := map[string]any{}
		findings := []privacyFinding{}
		keys := make([]string, 0, len(typed))
		for key := range typed {
			keys = append(keys, key)
		}
		sort.Strings(keys)
		for _, key := range keys {
			ruleID := forcedRule
			if ruleID == "" {
				ruleID = validator.keyRule(key, "json")
			}
			child, childFindings := validator.redactJSONValue(typed[key], findingContext, ruleID)
			output[key] = child
			findings = append(findings, childFindings...)
		}
		return output, findings
	case []any:
		output := make([]any, 0, len(typed))
		findings := []privacyFinding{}
		for _, item := range typed {
			child, childFindings := validator.redactJSONValue(item, findingContext, forcedRule)
			output = append(output, child)
			findings = append(findings, childFindings...)
		}
		return output, findings
	default:
		ruleID := forcedRule
		if ruleID == "" {
			if text, ok := typed.(string); ok {
				ruleID = scalarRule(text)
			}
		}
		if ruleID == "" {
			return value, []privacyFinding{}
		}
		return replacement(ruleID), []privacyFinding{finding(ruleID, findingContext)}
	}
}

func (validator privacyValidator) redactJSON(raw []byte, findingContext string) ([]byte, []privacyFinding, int, error) {
	value, err := decodeJSONValue(raw, validator.registry.Limits.MaxJSONDepth)
	if err != nil {
		return nil, nil, 0, err
	}
	depth, nodes := jsonMetrics(value, 1)
	if err := bounded(depth, validator.registry.Limits.MaxJSONDepth); err != nil {
		return nil, nil, 0, err
	}
	if err := bounded(nodes, validator.registry.Limits.MaxStructuredItems); err != nil {
		return nil, nil, 0, err
	}
	output, findings := validator.redactJSONValue(value, findingContext, "")
	canonical, err := canonicalJSON(output)
	return canonical, findings, nodes, err
}

func (validator privacyValidator) redactForm(raw []byte, findingContext string) ([]byte, []privacyFinding, int, error) {
	text, err := validUTF8(raw)
	if err != nil {
		return nil, nil, 0, err
	}
	fields := []string{}
	if text != "" {
		fields = strings.Split(text, "&")
	}
	if err := bounded(len(fields), validator.registry.Limits.MaxStructuredItems); err != nil {
		return nil, nil, 0, err
	}
	output := []string{}
	findings := []privacyFinding{}
	for _, field := range fields {
		if strings.Count(field, "=") != 1 {
			return nil, nil, 0, privacyFail("malformed_encoding")
		}
		key, value, _ := strings.Cut(field, "=")
		decodedKey, err := strictURLDecode(key)
		if err != nil {
			return nil, nil, 0, err
		}
		decodedValue, err := strictURLDecode(value)
		if err != nil {
			return nil, nil, 0, err
		}
		ruleID := validator.keyRule(decodedKey, "form")
		if ruleID == "" {
			ruleID = scalarRule(decodedValue)
		}
		if ruleID != "" {
			decodedValue = replacement(ruleID)
			findings = append(findings, finding(ruleID, findingContext))
		}
		output = append(output, string(percentEncode([]byte(decodedKey)))+"="+string(percentEncode([]byte(decodedValue))))
	}
	return []byte(strings.Join(output, "&")), findings, len(fields), nil
}

func (validator privacyValidator) redactContext(raw []byte, context string, findingContext string) ([]byte, []privacyFinding, int, error) {
	switch context {
	case "headers":
		return validator.redactHeaders(raw, findingContext)
	case "url":
		return validator.redactURL(raw, findingContext)
	case "json":
		return validator.redactJSON(raw, findingContext)
	case "form":
		return validator.redactForm(raw, findingContext)
	default:
		return nil, nil, 0, privacyFail("unknown_context")
	}
}

type extensionOuter struct {
	Encoding      string `json:"encoding"`
	PayloadB64    string `json:"payload_b64"`
	PayloadSHA256 string `json:"payload_sha256"`
	SchemaID      string `json:"schema_id"`
	SchemaVersion int    `json:"schema_version"`
}

type extensionHeader struct {
	Name  string `json:"name"`
	Value string `json:"value"`
}

type extensionInline struct {
	Context string `json:"context"`
	DataB64 string `json:"data_b64"`
}

func rawObjectKeys(raw []byte, maximumDepth int) (map[string]json.RawMessage, error) {
	if err := preflightJSON(raw, maximumDepth); err != nil {
		return nil, err
	}
	var value map[string]json.RawMessage
	if err := json.Unmarshal(raw, &value); err != nil {
		return nil, privacyFail("malformed_encoding")
	}
	return value, nil
}

func (validator privacyValidator) redactEnvelope(raw []byte) ([]byte, []privacyFinding, int, error) {
	outerKeys, err := rawObjectKeys(raw, validator.registry.Limits.MaxJSONDepth)
	if err != nil {
		return nil, nil, 0, err
	}
	if len(outerKeys) != 5 {
		return nil, nil, 0, privacyFail("malformed_encoding")
	}
	for _, key := range []string{"encoding", "payload_b64", "payload_sha256", "schema_id", "schema_version"} {
		if _, ok := outerKeys[key]; !ok {
			return nil, nil, 0, privacyFail("malformed_encoding")
		}
	}
	var outer extensionOuter
	if err := json.Unmarshal(raw, &outer); err != nil {
		return nil, nil, 0, privacyFail("malformed_encoding")
	}
	if outer.SchemaID != "jobseek.synthetic.capture" || outer.SchemaVersion != 1 || outer.Encoding != "canonical_json" {
		return nil, nil, 0, privacyFail("unsupported_envelope")
	}
	payload, err := strictBase64(outer.PayloadB64, "malformed_encoding")
	if err != nil {
		return nil, nil, 0, err
	}
	inner, err := rawObjectKeys(payload, validator.registry.Limits.MaxJSONDepth)
	if err != nil {
		return nil, nil, 0, err
	}
	if _, ok := inner["artifact"]; ok {
		return nil, nil, 0, privacyFail("artifact_unavailable")
	}
	if len(inner) != 2 || inner["inline"] == nil || inner["metadata"] == nil {
		return nil, nil, 0, privacyFail("malformed_encoding")
	}
	var metadata []extensionHeader
	var inline extensionInline
	if err := json.Unmarshal(inner["metadata"], &metadata); err != nil {
		return nil, nil, 0, privacyFail("malformed_encoding")
	}
	if err := json.Unmarshal(inner["inline"], &inline); err != nil {
		return nil, nil, 0, privacyFail("malformed_encoding")
	}
	if !contains([]string{"headers", "url", "json", "form"}, inline.Context) {
		return nil, nil, 0, privacyFail("unsupported_envelope")
	}
	findings := []privacyFinding{}
	for index := range metadata {
		ruleID := validator.keyRule(metadata[index].Name, "extension_envelope")
		if ruleID == "" {
			ruleID = scalarRule(metadata[index].Value)
		}
		if ruleID != "" {
			metadata[index].Value = replacement(ruleID)
			findings = append(findings, finding(ruleID, "extension_envelope"))
		}
	}
	inlineBytes, err := strictBase64(inline.DataB64, "malformed_encoding")
	if err != nil {
		return nil, nil, 0, err
	}
	inlineOutput, inlineFindings, itemCount, err := validator.redactContext(inlineBytes, inline.Context, "extension_envelope")
	if err != nil {
		return nil, nil, 0, err
	}
	findings = append(findings, inlineFindings...)
	if err := bounded(len(metadata)+itemCount, validator.registry.Limits.MaxStructuredItems); err != nil {
		return nil, nil, 0, err
	}
	safeInner := map[string]any{
		"inline": map[string]any{
			"context":  inline.Context,
			"data_b64": base64.StdEncoding.EncodeToString(inlineOutput),
		},
		"metadata": metadata,
	}
	encodedInner, err := canonicalJSON(safeInner)
	if err != nil {
		return nil, nil, 0, privacyFail("malformed_encoding")
	}
	safeOuter := extensionOuter{
		Encoding:      "canonical_json",
		PayloadB64:    base64.StdEncoding.EncodeToString(encodedInner),
		PayloadSHA256: "",
		SchemaID:      "jobseek.synthetic.capture",
		SchemaVersion: 1,
	}
	output, err := canonicalJSON(safeOuter)
	return output, findings, len(metadata) + itemCount, err
}

func (validator privacyValidator) transform(item privacyCase) privacyResult {
	reject := func(err error) privacyResult {
		return privacyResult{Status: "rejected", ErrorCode: privacyFailureCode(err, validator.registry)}
	}
	if !contains(validator.registry.Contexts, item.Context) {
		return reject(privacyFail("unknown_context"))
	}
	original, err := validator.loadInput(item.Input)
	if err != nil {
		return reject(err)
	}
	logical, err := validator.decodeWrapper(original, item.Wrapper)
	if err != nil {
		return reject(err)
	}
	var output []byte
	var findings []privacyFinding
	if item.Context == "extension_envelope" {
		output, findings, _, err = validator.redactEnvelope(logical)
	} else {
		output, findings, _, err = validator.redactContext(logical, item.Context, item.Context)
	}
	if err != nil {
		return reject(err)
	}
	wrappedOutput := encodeWrapper(output, item.Wrapper)
	if err := bounded(len(wrappedOutput), validator.registry.Limits.MaxOutput); err != nil {
		return reject(err)
	}
	if len(findings) == 0 && !bytes.Equal(wrappedOutput, original) {
		return reject(privacyFail("malformed_encoding"))
	}
	status := "unchanged"
	if len(findings) > 0 {
		status = "transformed"
	}
	return privacyResult{Status: status, Output: wrappedOutput, Findings: findings}
}

func safePrivacyResult(caseID string, result privacyResult) map[string]any {
	if result.Status == "rejected" {
		return map[string]any{"case_id": caseID, "error_code": result.ErrorCode, "status": "rejected"}
	}
	return map[string]any{
		"case_id":    caseID,
		"findings":   result.Findings,
		"output_b64": base64.StdEncoding.EncodeToString(result.Output),
		"status":     result.Status,
	}
}

func expectedPrivacyDigest(expected map[string]any) (string, error) {
	canonical, err := canonicalJSON(expected)
	if err != nil {
		return "", err
	}
	digest := sha256.Sum256(append([]byte(privacyResultDomain), canonical...))
	return hex.EncodeToString(digest[:]), nil
}
