package adjacentpolicy

// Candidate-only offline semantics conformance. This file has no runtime,
// persistence, artifact-resolution, or network authority.

import (
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/url"
	"sort"
	"strconv"
	"strings"
	"unicode"
	"unicode/utf8"
)

const (
	semanticsContentDomain  = "jobseek.runtime.v1.content-sha256\x00"
	semanticsMetadataDomain = "jobseek.runtime.v1.metadata-sha256\x00"
	semanticsResultDomain   = "jobseek.runtime.v1.semantic-sha256\x00"
	semanticsMaxHTMLBytes   = 1_048_576
	semanticsMaxHTMLNesting = 128
)

var semanticsLocales = map[string]string{
	"de":    "de",
	"de-ch": "de-CH",
	"de-de": "de-DE",
	"en":    "en",
	"en-ch": "en-CH",
	"en-gb": "en-GB",
	"en-us": "en-US",
	"fr":    "fr",
	"fr-ch": "fr-CH",
	"fr-fr": "fr-FR",
	"it":    "it",
	"it-ch": "it-CH",
	"it-it": "it-IT",
}

var semanticsIgnoredTags = map[string]bool{
	"noscript": true,
	"script":   true,
	"style":    true,
	"template": true,
}

var semanticsVoidTags = map[string]bool{
	"area": true, "base": true, "br": true, "col": true, "embed": true,
	"hr": true, "img": true, "input": true, "link": true, "meta": true,
	"param": true, "source": true, "track": true, "wbr": true,
}

var semanticsJobFields = map[string]bool{
	"base_salary": true, "date_posted": true, "description_html": true,
	"employment_type": true, "extensions": true, "job_location_type": true,
	"language": true, "localizations": true, "locations": true,
	"skills": true, "title": true,
}

var semanticsLocalizationFields = map[string]bool{
	"description_html": true, "locale": true, "title": true,
}

type semanticFailure struct {
	reason   string
	rejected bool
}

type semanticTargetTuple struct {
	source  string
	content map[string]any
}

type semanticHTMLFrame struct {
	tag        string
	suppresses bool
}

// ProjectedResult is the closed offline result shape used by semantics fixtures.
type ProjectedResult map[string]any

func semanticsFailure(reason string) *semanticFailure {
	return &semanticFailure{reason: reason}
}

func semanticsRejected(reason string) *semanticFailure {
	return &semanticFailure{reason: reason, rejected: true}
}

func semanticsValidateJSON(value any) error {
	switch typed := value.(type) {
	case nil, bool, int, int64, uint64:
		return nil
	case json.Number:
		if strings.ContainsAny(typed.String(), ".eE") {
			return fmt.Errorf("non-integer JSON number")
		}
		if strings.HasPrefix(typed.String(), "-") {
			if _, err := strconv.ParseInt(typed.String(), 10, 64); err != nil {
				return err
			}
		} else if _, err := strconv.ParseUint(typed.String(), 10, 64); err != nil {
			return err
		}
		return nil
	case string:
		if !utf8.ValidString(typed) {
			return fmt.Errorf("invalid UTF-8")
		}
		return nil
	case []any:
		for _, item := range typed {
			if err := semanticsValidateJSON(item); err != nil {
				return err
			}
		}
		return nil
	case map[string]any:
		for key, item := range typed {
			if !utf8.ValidString(key) {
				return fmt.Errorf("invalid UTF-8 key")
			}
			if err := semanticsValidateJSON(item); err != nil {
				return err
			}
		}
		return nil
	default:
		return fmt.Errorf("unsupported semantics JSON type %T", value)
	}
}

func semanticsCanonicalJSON(value any) ([]byte, error) {
	if err := semanticsValidateJSON(value); err != nil {
		return nil, err
	}
	return canonicalJSON(value)
}

func semanticsLengthPrefixed(value []byte) []byte {
	output := make([]byte, 8+len(value))
	binary.BigEndian.PutUint64(output[:8], uint64(len(value)))
	copy(output[8:], value)
	return output
}

func semanticsDigest(domain string, fields ...[]byte) string {
	hash := sha256.New()
	hash.Write([]byte(domain))
	for _, field := range fields {
		hash.Write(semanticsLengthPrefixed(field))
	}
	return hex.EncodeToString(hash.Sum(nil))
}

func semanticsContentSHA256(canonicalURL string, canonicalJob map[string]any) (string, error) {
	jobJSON, err := semanticsCanonicalJSON(canonicalJob)
	if err != nil {
		return "", err
	}
	return semanticsDigest(semanticsContentDomain, []byte(canonicalURL), jobJSON), nil
}

func semanticsMetadataSHA256(targetURL string, metadata map[string]any) (string, error) {
	metadataJSON, err := semanticsCanonicalJSON(metadata)
	if err != nil {
		return "", err
	}
	return semanticsDigest(semanticsMetadataDomain, []byte(targetURL), metadataJSON), nil
}

func semanticsResultSHA256(result map[string]any) (string, error) {
	resultJSON, err := semanticsCanonicalJSON(result)
	if err != nil {
		return "", err
	}
	return semanticsDigest(semanticsResultDomain, resultJSON), nil
}

func semanticsASCIIToLower(value string) string {
	output := []byte(value)
	for index, character := range output {
		if character >= 'A' && character <= 'Z' {
			output[index] = character + ('a' - 'A')
		}
	}
	return string(output)
}

func semanticsASCII(value string) bool {
	for _, character := range []byte(value) {
		if character >= utf8.RuneSelf {
			return false
		}
	}
	return true
}

func semanticsURLForbidden(value string) bool {
	if !utf8.ValidString(value) {
		return true
	}
	for _, character := range value {
		if character == '\\' || unicode.IsSpace(character) || unicode.IsControl(character) {
			return true
		}
	}
	return false
}

func semanticsHexNibble(value byte) (byte, bool) {
	switch {
	case value >= '0' && value <= '9':
		return value - '0', true
	case value >= 'a' && value <= 'f':
		return value - 'a' + 10, true
	case value >= 'A' && value <= 'F':
		return value - 'A' + 10, true
	default:
		return 0, false
	}
}

func semanticsUnreserved(value byte) bool {
	return value >= 'a' && value <= 'z' || value >= 'A' && value <= 'Z' ||
		value >= '0' && value <= '9' || strings.ContainsRune("-._~", rune(value))
}

func semanticsComponentSafe(value byte, query bool) bool {
	if semanticsUnreserved(value) {
		return true
	}
	if query {
		return strings.ContainsRune("!$'()*+,-./:;=?@", rune(value))
	}
	return strings.ContainsRune("!$&'()*+,;=:@/", rune(value))
}

func semanticsCanonicalComponent(value string, query bool) (string, *semanticFailure) {
	if !utf8.ValidString(value) {
		return "", semanticsFailure("invalid_url")
	}
	const hexadecimal = "0123456789ABCDEF"
	var output strings.Builder
	for index := 0; index < len(value); {
		character := value[index]
		if character == '%' {
			if index+2 >= len(value) {
				return "", semanticsFailure("invalid_url")
			}
			high, highOK := semanticsHexNibble(value[index+1])
			low, lowOK := semanticsHexNibble(value[index+2])
			if !highOK || !lowOK {
				return "", semanticsFailure("invalid_url")
			}
			decoded := high<<4 | low
			if semanticsUnreserved(decoded) {
				output.WriteByte(decoded)
			} else {
				output.WriteByte('%')
				output.WriteByte(hexadecimal[decoded>>4])
				output.WriteByte(hexadecimal[decoded&15])
			}
			index += 3
			continue
		}
		if character < utf8.RuneSelf && semanticsComponentSafe(character, query) {
			output.WriteByte(character)
		} else {
			output.WriteByte('%')
			output.WriteByte(hexadecimal[character>>4])
			output.WriteByte(hexadecimal[character&15])
		}
		index++
	}
	return output.String(), nil
}

func semanticsRemoveDotSegments(path string) (string, *semanticFailure) {
	segments := strings.Split(path, "/")
	output := make([]string, 0, len(segments))
	for index, segment := range segments {
		switch segment {
		case ".":
			continue
		case "..":
			if len(output) == 0 || len(output) == 1 && output[0] == "" {
				return "", semanticsFailure("invalid_url")
			}
			output = output[:len(output)-1]
		default:
			if index == 0 && segment != "" {
				return "", semanticsFailure("invalid_url")
			}
			output = append(output, segment)
		}
	}
	canonical := strings.Join(output, "/")
	if canonical == "" {
		canonical = "/"
	}
	return canonical, nil
}

func semanticsIPv4Literal(host string) bool {
	parts := strings.Split(host, ".")
	allDecimal := true
	for _, part := range parts {
		if part == "" {
			return false
		}
		for _, character := range []byte(part) {
			if character < '0' || character > '9' {
				allDecimal = false
				break
			}
		}
	}
	if allDecimal {
		return true
	}
	return len(parts) == 1 && strings.HasPrefix(host, "0x") &&
		strings.Trim(host[2:], "0123456789abcdef") == ""
}

type semanticsQueryField struct {
	key       string
	value     string
	hasEquals bool
	ordinal   int
	rendered  string
}

func semanticsCanonicalURL(value any) (string, *semanticFailure) {
	raw, ok := value.(string)
	if !ok || raw == "" || semanticsURLForbidden(raw) {
		return "", semanticsFailure("invalid_url")
	}
	parsed, err := url.Parse(raw)
	if err != nil || parsed.Opaque != "" || parsed.Host == "" || parsed.User != nil {
		return "", semanticsFailure("invalid_url")
	}
	scheme := semanticsASCIIToLower(parsed.Scheme)
	if scheme != "http" && scheme != "https" {
		return "", semanticsFailure("invalid_url")
	}
	if strings.ContainsAny(parsed.Host, "[]@") || strings.Count(parsed.Host, ":") > 1 {
		return "", semanticsFailure("invalid_url")
	}
	host := parsed.Host
	port := ""
	var portNumber uint64
	if separator := strings.LastIndexByte(host, ':'); separator >= 0 {
		port = host[separator+1:]
		host = host[:separator]
		if port == "" {
			return "", semanticsFailure("invalid_url")
		}
		for _, character := range []byte(port) {
			if character < '0' || character > '9' {
				return "", semanticsFailure("invalid_url")
			}
		}
		parsedPort, parseErr := strconv.ParseUint(port, 10, 16)
		if parseErr != nil || parsedPort == 0 || parsedPort > 65535 {
			return "", semanticsFailure("invalid_url")
		}
		portNumber = parsedPort
		port = strconv.FormatUint(parsedPort, 10)
	}
	if host == "" || !semanticsASCII(host) || strings.HasSuffix(host, ".") || strings.Contains(host, "%") {
		return "", semanticsFailure("invalid_url")
	}
	host = semanticsASCIIToLower(host)
	if semanticsIPv4Literal(host) {
		return "", semanticsFailure("invalid_url")
	}
	if host != "localhost" {
		if len(host) > 253 {
			return "", semanticsFailure("invalid_url")
		}
		for _, label := range strings.Split(host, ".") {
			if len(label) == 0 || len(label) > 63 || label[0] == '-' || label[len(label)-1] == '-' {
				return "", semanticsFailure("invalid_url")
			}
			for _, character := range []byte(label) {
				if !(character >= 'a' && character <= 'z' || character >= '0' && character <= '9' || character == '-') {
					return "", semanticsFailure("invalid_url")
				}
			}
		}
	}
	if port != "" && (scheme == "http" && portNumber == 80 || scheme == "https" && portNumber == 443) {
		port = ""
	}
	authority := host
	if port != "" {
		authority += ":" + port
	}
	rawPath := parsed.EscapedPath()
	if rawPath == "" {
		rawPath = "/"
	}
	path, failure := semanticsCanonicalComponent(rawPath, false)
	if failure != nil {
		return "", failure
	}
	path, failure = semanticsRemoveDotSegments(path)
	if failure != nil {
		return "", failure
	}
	if _, fragmentFailure := semanticsCanonicalComponent(parsed.EscapedFragment(), true); fragmentFailure != nil {
		return "", fragmentFailure
	}
	fields := make([]semanticsQueryField, 0)
	if parsed.RawQuery != "" {
		for ordinal, field := range strings.Split(parsed.RawQuery, "&") {
			hasEquals := strings.Contains(field, "=")
			keySource, valueSource := field, ""
			if hasEquals {
				keySource, valueSource, _ = strings.Cut(field, "=")
			}
			key, keyFailure := semanticsCanonicalComponent(keySource, true)
			if keyFailure != nil {
				return "", keyFailure
			}
			fieldValue, valueFailure := semanticsCanonicalComponent(valueSource, true)
			if valueFailure != nil {
				return "", valueFailure
			}
			rendered := key
			if hasEquals {
				rendered += "=" + fieldValue
			}
			fields = append(fields, semanticsQueryField{key, fieldValue, hasEquals, ordinal, rendered})
		}
	}
	sort.SliceStable(fields, func(left, right int) bool {
		if fields[left].key != fields[right].key {
			return fields[left].key < fields[right].key
		}
		if fields[left].value != fields[right].value {
			return fields[left].value < fields[right].value
		}
		if fields[left].hasEquals != fields[right].hasEquals {
			return !fields[left].hasEquals
		}
		return fields[left].ordinal < fields[right].ordinal
	})
	query := make([]string, len(fields))
	for index, field := range fields {
		query[index] = field.rendered
	}
	canonical := scheme + "://" + authority + path
	if len(query) > 0 {
		canonical += "?" + strings.Join(query, "&")
	}
	return canonical, nil
}

func semanticsCanonicalLocale(value any) (string, *semanticFailure) {
	raw, ok := value.(string)
	if !ok || !semanticsASCII(raw) {
		return "", semanticsFailure("invalid_locale")
	}
	canonical, ok := semanticsLocales[semanticsASCIIToLower(strings.ReplaceAll(raw, "_", "-"))]
	if !ok {
		return "", semanticsFailure("invalid_locale")
	}
	return canonical, nil
}

func semanticsNonVisible(character rune) bool {
	return character >= 0x09 && character <= 0x0d || character == 0x20 ||
		character == 0x85 || character == 0xa0 || character == 0x1680 ||
		character >= 0x2000 && character <= 0x200b || character == 0x2028 ||
		character == 0x2029 || character == 0x202f || character == 0x205f ||
		character == 0x3000 || character == 0xfeff
}

func semanticsVisibleText(value string) bool {
	for offset := 0; offset < len(value); {
		if value[offset] == '&' {
			remaining := value[offset:]
			semicolon := strings.IndexByte(remaining, ';')
			if semicolon >= 0 {
				entity := remaining[1:semicolon]
				lower := semanticsASCIIToLower(entity)
				if lower == "nbsp" {
					offset += semicolon + 1
					continue
				}
				if strings.HasPrefix(lower, "#") {
					base, digits := 10, lower[1:]
					if strings.HasPrefix(digits, "x") {
						base, digits = 16, digits[1:]
					}
					if scalar, err := strconv.ParseUint(digits, base, 32); err == nil && scalar == 0xa0 {
						offset += semicolon + 1
						continue
					}
				}
			}
			return true
		}
		character, size := utf8.DecodeRuneInString(value[offset:])
		if !semanticsNonVisible(character) {
			return true
		}
		offset += size
	}
	return false
}

func semanticsHTMLControlInvalid(value string) bool {
	if !utf8.ValidString(value) {
		return true
	}
	for _, character := range value {
		if character == 0 || character < 0x20 && !(character >= 0x09 && character <= 0x0d) ||
			character >= 0x7f && character <= 0x9f && character != 0x85 {
			return true
		}
	}
	return false
}

func semanticsFindTagEnd(value string, start int) int {
	var quote byte
	for index := start; index < len(value); index++ {
		character := value[index]
		if quote != 0 {
			if character == quote {
				quote = 0
			}
			continue
		}
		if character == '\'' || character == '"' {
			quote = character
			continue
		}
		if character == '>' {
			return index
		}
	}
	return -1
}

func semanticsAttributeHidden(source string) (bool, *semanticFailure) {
	index := 0
	for index < len(source) && !unicode.IsSpace(rune(source[index])) && source[index] != '/' {
		index++
	}
	for index < len(source) {
		for index < len(source) && unicode.IsSpace(rune(source[index])) {
			index++
		}
		if index >= len(source) || source[index] == '/' {
			break
		}
		start := index
		for index < len(source) && !unicode.IsSpace(rune(source[index])) && source[index] != '=' && source[index] != '/' {
			index++
		}
		if start == index {
			return false, semanticsFailure("invalid_visible_content")
		}
		name := semanticsASCIIToLower(source[start:index])
		for index < len(source) && unicode.IsSpace(rune(source[index])) {
			index++
		}
		attributeValue := ""
		if index < len(source) && source[index] == '=' {
			index++
			for index < len(source) && unicode.IsSpace(rune(source[index])) {
				index++
			}
			if index >= len(source) {
				return false, semanticsFailure("invalid_visible_content")
			}
			if source[index] == '\'' || source[index] == '"' {
				quote := source[index]
				index++
				start = index
				for index < len(source) && source[index] != quote {
					index++
				}
				if index >= len(source) {
					return false, semanticsFailure("invalid_visible_content")
				}
				attributeValue = source[start:index]
				index++
			} else {
				start = index
				for index < len(source) && !unicode.IsSpace(rune(source[index])) && source[index] != '/' {
					index++
				}
				attributeValue = source[start:index]
			}
		}
		switch name {
		case "hidden":
			return true, nil
		case "aria-hidden":
			if semanticsASCIIToLower(attributeValue) == "true" {
				return true, nil
			}
		case "style":
			for _, declaration := range strings.Split(attributeValue, ";") {
				property, propertyValue, found := strings.Cut(declaration, ":")
				if !found {
					continue
				}
				pair := semanticsASCIIToLower(strings.Trim(property, " \t\n\r\f")) + ":" +
					semanticsASCIIToLower(strings.Trim(propertyValue, " \t\n\r\f"))
				if pair == "display:none" || pair == "visibility:hidden" || pair == "visibility:collapse" {
					return true, nil
				}
			}
		}
	}
	return false, nil
}

func semanticsHasVisibleContent(value any) (bool, *semanticFailure) {
	html, ok := value.(string)
	if !ok {
		return false, semanticsRejected("invalid_projection")
	}
	if len([]byte(html)) > semanticsMaxHTMLBytes || semanticsHTMLControlInvalid(html) {
		return false, semanticsFailure("invalid_visible_content")
	}
	stack := make([]semanticHTMLFrame, 0)
	suppressedDepth := 0
	visible := false
	for offset := 0; offset < len(html); {
		if html[offset] != '<' {
			next := strings.IndexByte(html[offset:], '<')
			if next < 0 {
				next = len(html) - offset
			}
			if suppressedDepth == 0 && semanticsVisibleText(html[offset:offset+next]) {
				visible = true
			}
			offset += next
			continue
		}
		if strings.HasPrefix(html[offset:], "<!--") {
			end := strings.Index(html[offset+4:], "-->")
			if end < 0 {
				return false, semanticsFailure("invalid_visible_content")
			}
			offset += 4 + end + 3
			continue
		}
		end := semanticsFindTagEnd(html, offset+1)
		if end < 0 {
			return false, semanticsFailure("invalid_visible_content")
		}
		token := strings.TrimSpace(html[offset+1 : end])
		offset = end + 1
		if token == "" {
			return false, semanticsFailure("invalid_visible_content")
		}
		if token[0] == '!' || token[0] == '?' {
			continue
		}
		if token[0] == '/' {
			tag := semanticsASCIIToLower(strings.TrimSpace(token[1:]))
			if len(stack) == 0 {
				continue
			}
			last := stack[len(stack)-1]
			if tag != last.tag {
				if suppressedDepth > 0 {
					return false, semanticsFailure("invalid_visible_content")
				}
				continue
			}
			stack = stack[:len(stack)-1]
			if last.suppresses {
				suppressedDepth--
			}
			continue
		}
		selfClosing := strings.HasSuffix(token, "/")
		tagEnd := 0
		for tagEnd < len(token) && !unicode.IsSpace(rune(token[tagEnd])) && token[tagEnd] != '/' {
			tagEnd++
		}
		if tagEnd == 0 {
			return false, semanticsFailure("invalid_visible_content")
		}
		tag := semanticsASCIIToLower(token[:tagEnd])
		hidden, failure := semanticsAttributeHidden(token)
		if failure != nil {
			return false, failure
		}
		if selfClosing || semanticsVoidTags[tag] {
			continue
		}
		if len(stack) >= semanticsMaxHTMLNesting {
			return false, semanticsFailure("invalid_visible_content")
		}
		suppresses := semanticsIgnoredTags[tag] || hidden
		stack = append(stack, semanticHTMLFrame{tag: tag, suppresses: suppresses})
		if suppresses {
			suppressedDepth++
		}
	}
	for _, frame := range stack {
		if frame.suppresses {
			return false, semanticsFailure("invalid_visible_content")
		}
	}
	return visible, nil
}

func semanticsClone(value any) (any, error) {
	switch typed := value.(type) {
	case nil, bool, json.Number, string:
		return typed, nil
	case []any:
		output := make([]any, len(typed))
		for index, item := range typed {
			cloned, err := semanticsClone(item)
			if err != nil {
				return nil, err
			}
			output[index] = cloned
		}
		return output, nil
	case map[string]any:
		output := make(map[string]any, len(typed))
		for key, item := range typed {
			cloned, err := semanticsClone(item)
			if err != nil {
				return nil, err
			}
			output[key] = cloned
		}
		return output, nil
	default:
		return nil, fmt.Errorf("unsupported input type %T", value)
	}
}

func semanticsCanonicalSet(value any) ([]any, *semanticFailure) {
	items, ok := value.([]any)
	if !ok {
		return nil, semanticsRejected("invalid_projection")
	}
	unique := map[string]bool{}
	for _, item := range items {
		text, ok := item.(string)
		if !ok {
			return nil, semanticsRejected("invalid_projection")
		}
		unique[text] = true
	}
	values := make([]string, 0, len(unique))
	for item := range unique {
		values = append(values, item)
	}
	sort.Strings(values)
	output := make([]any, len(values))
	for index, item := range values {
		output[index] = item
	}
	return output, nil
}

type semanticLocalization struct {
	source string
	value  map[string]any
}

func semanticsCanonicalJob(value any) (map[string]any, *semanticFailure) {
	input, ok := value.(map[string]any)
	if !ok {
		return nil, semanticsRejected("invalid_projection")
	}
	for key := range input {
		if !semanticsJobFields[key] {
			return nil, semanticsRejected("invalid_projection")
		}
	}
	cloned, err := semanticsClone(input)
	if err != nil {
		return nil, semanticsRejected("invalid_projection")
	}
	output := cloned.(map[string]any)
	descriptions := make([]any, 0)
	if description, present := output["description_html"]; present {
		if _, ok := description.(string); !ok {
			return nil, semanticsRejected("invalid_projection")
		}
		descriptions = append(descriptions, description)
	}
	if skills, present := output["skills"]; present {
		canonical, failure := semanticsCanonicalSet(skills)
		if failure != nil {
			return nil, failure
		}
		output["skills"] = canonical
	}
	if locationsValue, present := output["locations"]; present {
		locations, ok := locationsValue.(map[string]any)
		if !ok || len(locations) != 1 {
			return nil, semanticsRejected("invalid_projection")
		}
		values, present := locations["values"]
		if !present {
			return nil, semanticsRejected("invalid_projection")
		}
		canonical, failure := semanticsCanonicalSet(values)
		if failure != nil {
			return nil, failure
		}
		locations["values"] = canonical
	}
	if language, present := output["language"]; present {
		canonical, failure := semanticsCanonicalLocale(language)
		if failure != nil {
			return nil, failure
		}
		output["language"] = canonical
	}
	localizationsValue, present := output["localizations"]
	if !present {
		localizationsValue = []any{}
	}
	localizations, ok := localizationsValue.([]any)
	if !ok {
		return nil, semanticsRejected("invalid_projection")
	}
	byLocale := map[string]semanticLocalization{}
	for _, item := range localizations {
		localization, ok := item.(map[string]any)
		if !ok {
			return nil, semanticsRejected("invalid_projection")
		}
		for key := range localization {
			if !semanticsLocalizationFields[key] {
				return nil, semanticsRejected("invalid_projection")
			}
		}
		source, present := localization["locale"]
		if !present {
			return nil, semanticsRejected("invalid_projection")
		}
		locale, failure := semanticsCanonicalLocale(source)
		if failure != nil {
			return nil, failure
		}
		canonicalValue, cloneErr := semanticsClone(localization)
		if cloneErr != nil {
			return nil, semanticsRejected("invalid_projection")
		}
		canonical := canonicalValue.(map[string]any)
		canonical["locale"] = locale
		if description, present := canonical["description_html"]; present {
			if _, ok := description.(string); !ok {
				return nil, semanticsRejected("invalid_projection")
			}
			descriptions = append(descriptions, description)
		}
		if previous, duplicate := byLocale[locale]; duplicate {
			previousJSON, _ := semanticsCanonicalJSON(previous.value)
			canonicalJSONValue, _ := semanticsCanonicalJSON(canonical)
			if previous.source != source || string(previousJSON) != string(canonicalJSONValue) {
				return nil, semanticsFailure("canonical_collision")
			}
			continue
		}
		sourceText, ok := source.(string)
		if !ok {
			return nil, semanticsFailure("invalid_locale")
		}
		byLocale[locale] = semanticLocalization{source: sourceText, value: canonical}
	}
	locales := make([]string, 0, len(byLocale))
	for locale := range byLocale {
		locales = append(locales, locale)
	}
	sort.Strings(locales)
	canonicalLocalizations := make([]any, len(locales))
	for index, locale := range locales {
		canonicalLocalizations[index] = byLocale[locale].value
	}
	output["localizations"] = canonicalLocalizations
	visible := false
	for _, description := range descriptions {
		candidate, failure := semanticsHasVisibleContent(description)
		if failure != nil {
			return nil, failure
		}
		visible = visible || candidate
	}
	if !visible {
		return nil, semanticsFailure("invalid_visible_content")
	}
	if _, err := semanticsCanonicalJSON(output); err != nil {
		return nil, semanticsRejected("invalid_projection")
	}
	return output, nil
}

func semanticsObject(value any) (map[string]any, *semanticFailure) {
	object, ok := value.(map[string]any)
	if !ok {
		return nil, semanticsRejected("invalid_projection")
	}
	return object, nil
}

func semanticsShape(value any, required, optional map[string]bool) (map[string]any, *semanticFailure) {
	object, failure := semanticsObject(value)
	if failure != nil {
		return nil, failure
	}
	for key := range required {
		if _, present := object[key]; !present {
			return nil, semanticsRejected("invalid_projection")
		}
	}
	for key := range object {
		if !required[key] && !optional[key] {
			return nil, semanticsRejected("invalid_projection")
		}
	}
	return object, nil
}

func semanticsValidateExistingEffects(value any) *semanticFailure {
	effects, ok := value.(map[string]any)
	if !ok {
		return semanticsRejected("invalid_projection")
	}
	urls, urlsOK := effects["urls_to_upsert"].([]any)
	hashes, hashesOK := effects["content_hashes"].([]any)
	jobs, jobsOK := effects["job_effects"].([]any)
	targets, targetsOK := effects["targets"].([]any)
	if !urlsOK || !hashesOK || !jobsOK || !targetsOK || len(urls) != len(hashes) ||
		len(urls) != len(jobs) || len(urls) != len(targets) {
		return semanticsRejected("invalid_projection")
	}
	for index, rawURL := range urls {
		canonical, failure := semanticsCanonicalURL(rawURL)
		if failure != nil {
			return semanticsRejected("invalid_projection")
		}
		digest, digestOK := hashes[index].(string)
		job, jobOK := jobs[index].(map[string]any)
		target, targetOK := targets[index].(map[string]any)
		if !digestOK || !jobOK || !targetOK || job["source_url"] != canonical ||
			job["content_sha256"] != digest || target["url"] != canonical {
			return semanticsRejected("invalid_projection")
		}
		targetDigest, present := target["content_sha256"]
		if !present {
			targetDigest = ""
		}
		if targetDigest != digest {
			return semanticsRejected("invalid_projection")
		}
	}
	return nil
}

func semanticsString(object map[string]any, key string) (string, *semanticFailure) {
	value, ok := object[key].(string)
	if !ok || value == "" {
		return "", semanticsRejected("invalid_projection")
	}
	return value, nil
}

func semanticsUint64(value any) (uint64, *semanticFailure) {
	number, ok := value.(json.Number)
	if !ok {
		return 0, semanticsRejected("invalid_projection")
	}
	if strings.HasPrefix(number.String(), "-") || strings.ContainsAny(number.String(), ".eE") {
		return 0, semanticsRejected("invalid_projection")
	}
	parsed, err := strconv.ParseUint(number.String(), 10, 64)
	if err != nil {
		return 0, semanticsFailure("counter_overflow")
	}
	return parsed, nil
}

func semanticsBaseEffects(request map[string]any, executionKind, targetURL string) (map[string]any, *semanticFailure) {
	requestID, failure := semanticsString(request, "request_id")
	if failure != nil {
		return nil, failure
	}
	originRequestID, failure := semanticsString(request, "origin_request_id")
	if failure != nil {
		return nil, failure
	}
	return map[string]any{
		"canonicalization_rule":   "CANONICALIZATION_RULE_RUNTIME_V1",
		"content_hash_rule":       "HASH_RULE_CONTENT_SHA256_V1",
		"content_hashes":          []any{},
		"execution_kind":          executionKind,
		"filtered_count":          uint64(0),
		"gone_detection_allowed":  false,
		"hybrid":                  false,
		"job_effects":             []any{},
		"origin_request_id":       originRequestID,
		"request_id":              requestID,
		"security_filtered_count": uint64(0),
		"target_url":              targetURL,
		"targets":                 []any{},
		"truncated":               false,
		"urls_to_upsert":          []any{},
	}, nil
}

func semanticsAddTarget(effects map[string]any, targetURL string, digest *string) {
	sentinel := ""
	if digest != nil {
		sentinel = *digest
	}
	effects["urls_to_upsert"] = append(effects["urls_to_upsert"].([]any), targetURL)
	effects["content_hashes"] = append(effects["content_hashes"].([]any), sentinel)
	effects["job_effects"] = append(effects["job_effects"].([]any), map[string]any{
		"content_sha256": sentinel,
		"source_url":     targetURL,
	})
	target := map[string]any{"action": "PROJECTED_ACTION_UPSERT", "url": targetURL}
	if digest != nil {
		target["content_sha256"] = *digest
	}
	effects["targets"] = append(effects["targets"].([]any), target)
}

func semanticsProjectScrape(input map[string]any) (map[string]any, *semanticFailure) {
	request, failure := semanticsShape(
		input["request"],
		map[string]bool{"source_url": true},
		map[string]bool{"origin_request_id": true, "request_id": true},
	)
	if failure != nil {
		return nil, failure
	}
	result, failure := semanticsShape(
		input["result"],
		map[string]bool{"content": true},
		nil,
	)
	if failure != nil {
		return nil, failure
	}
	sourceURL, failure := semanticsCanonicalURL(request["source_url"])
	if failure != nil {
		return nil, failure
	}
	content, failure := semanticsCanonicalJob(result["content"])
	if failure != nil {
		return nil, failure
	}
	digest, err := semanticsContentSHA256(sourceURL, content)
	if err != nil {
		return nil, semanticsRejected("invalid_projection")
	}
	effects, failure := semanticsBaseEffects(request, "EXECUTION_KIND_SCRAPE", sourceURL)
	if failure != nil {
		return nil, failure
	}
	semanticsAddTarget(effects, sourceURL, &digest)
	return effects, nil
}

func semanticsCheckedAdd(left, right uint64) (uint64, *semanticFailure) {
	if ^uint64(0)-left < right {
		return 0, semanticsFailure("counter_overflow")
	}
	return left + right, nil
}

func semanticsMonitorResultShape(value any) (map[string]any, *semanticFailure) {
	return semanticsShape(
		value,
		map[string]bool{
			"filtered_count": true, "hybrid": true, "jobs": true,
			"security_filtered_count": true, "truncated": true, "urls": true,
		},
		map[string]bool{"metadata_updates": true, "new_sitemap_url": true},
	)
}

func semanticsMonitorResults(input map[string]any) ([]map[string]any, *semanticFailure) {
	_, hasResult := input["result"]
	batchesValue, hasBatches := input["batches"]
	if hasResult == hasBatches {
		return nil, semanticsRejected("invalid_projection")
	}
	if hasResult {
		result, failure := semanticsMonitorResultShape(input["result"])
		if failure != nil {
			return nil, failure
		}
		return []map[string]any{result}, nil
	}
	batches, ok := batchesValue.([]any)
	if !ok || len(batches) == 0 {
		return nil, semanticsRejected("invalid_projection")
	}
	results := make([]map[string]any, 0, len(batches))
	var checkedTotal uint64
	for _, batchValue := range batches {
		batch, failure := semanticsShape(
			batchValue,
			map[string]bool{"checked_count": true, "complete": true, "result": true},
			nil,
		)
		if failure != nil {
			return nil, failure
		}
		checked, failure := semanticsUint64(batch["checked_count"])
		if failure != nil {
			return nil, failure
		}
		checkedTotal, failure = semanticsCheckedAdd(checkedTotal, checked)
		if failure != nil {
			return nil, failure
		}
		complete, ok := batch["complete"].(bool)
		if !ok {
			return nil, semanticsRejected("invalid_projection")
		}
		if !complete {
			return nil, semanticsFailure("ineligible_history")
		}
		result, failure := semanticsMonitorResultShape(batch["result"])
		if failure != nil {
			return nil, failure
		}
		results = append(results, result)
	}
	return results, nil
}

func semanticsProjectMonitor(input map[string]any) (map[string]any, *semanticFailure) {
	request, failure := semanticsShape(
		input["request"],
		map[string]bool{"target_url": true},
		map[string]bool{"origin_request_id": true, "request_id": true},
	)
	if failure != nil {
		return nil, failure
	}
	targetURL, failure := semanticsCanonicalURL(request["target_url"])
	if failure != nil {
		return nil, failure
	}
	results, failure := semanticsMonitorResults(input)
	if failure != nil {
		return nil, failure
	}
	tuples := map[string]semanticTargetTuple{}
	var filteredCount, securityFilteredCount uint64
	hybrid, truncated := false, false
	sitemaps := make([]string, 0)
	metadataSequence := make([]any, 0, len(results))
	for _, result := range results {
		urls, urlsOK := result["urls"].([]any)
		jobs, jobsOK := result["jobs"].([]any)
		if !urlsOK || !jobsOK {
			return nil, semanticsRejected("invalid_projection")
		}
		for _, sourceValue := range urls {
			source, ok := sourceValue.(string)
			if !ok {
				return nil, semanticsFailure("invalid_url")
			}
			canonical, canonicalFailure := semanticsCanonicalURL(source)
			if canonicalFailure != nil {
				return nil, canonicalFailure
			}
			if previous, duplicate := tuples[canonical]; duplicate && previous.source != source {
				return nil, semanticsFailure("canonical_collision")
			}
			if _, present := tuples[canonical]; !present {
				tuples[canonical] = semanticTargetTuple{source: source}
			}
		}
		for _, discoveredValue := range jobs {
			discovered, ok := discoveredValue.(map[string]any)
			if !ok || len(discovered) != 2 {
				return nil, semanticsRejected("invalid_projection")
			}
			source, ok := discovered["url"].(string)
			if !ok {
				return nil, semanticsFailure("invalid_url")
			}
			canonical, canonicalFailure := semanticsCanonicalURL(source)
			if canonicalFailure != nil {
				return nil, canonicalFailure
			}
			content, contentFailure := semanticsCanonicalJob(discovered["content"])
			if contentFailure != nil {
				return nil, contentFailure
			}
			if previous, duplicate := tuples[canonical]; duplicate {
				if previous.source != source {
					return nil, semanticsFailure("canonical_collision")
				}
				if previous.content != nil {
					previousJSON, _ := semanticsCanonicalJSON(previous.content)
					contentJSON, _ := semanticsCanonicalJSON(content)
					if string(previousJSON) != string(contentJSON) {
						return nil, semanticsFailure("canonical_collision")
					}
				}
			}
			tuples[canonical] = semanticTargetTuple{source: source, content: content}
		}
		filtered, countFailure := semanticsUint64(result["filtered_count"])
		if countFailure != nil {
			return nil, countFailure
		}
		filteredCount, countFailure = semanticsCheckedAdd(filteredCount, filtered)
		if countFailure != nil {
			return nil, countFailure
		}
		securityFiltered, countFailure := semanticsUint64(result["security_filtered_count"])
		if countFailure != nil {
			return nil, countFailure
		}
		securityFilteredCount, countFailure = semanticsCheckedAdd(securityFilteredCount, securityFiltered)
		if countFailure != nil {
			return nil, countFailure
		}
		resultHybrid, hybridOK := result["hybrid"].(bool)
		resultTruncated, truncatedOK := result["truncated"].(bool)
		if !hybridOK || !truncatedOK {
			return nil, semanticsRejected("invalid_projection")
		}
		hybrid = hybrid || resultHybrid
		truncated = truncated || resultTruncated
		if sitemapValue, present := result["new_sitemap_url"]; present {
			sitemap, sitemapFailure := semanticsCanonicalURL(sitemapValue)
			if sitemapFailure != nil {
				return nil, sitemapFailure
			}
			sitemaps = append(sitemaps, sitemap)
		}
		metadata, present := result["metadata_updates"]
		if present {
			if _, ok := metadata.(map[string]any); !ok {
				return nil, semanticsRejected("invalid_projection")
			}
			if _, err := semanticsCanonicalJSON(metadata); err != nil {
				return nil, semanticsRejected("invalid_projection")
			}
			metadataSequence = append(metadataSequence, metadata)
		} else {
			metadataSequence = append(metadataSequence, nil)
		}
	}
	if len(sitemaps) > 1 {
		for _, sitemap := range sitemaps[1:] {
			if sitemap != sitemaps[0] {
				return nil, semanticsFailure("canonical_collision")
			}
		}
	}
	effects, failure := semanticsBaseEffects(request, "EXECUTION_KIND_MONITOR", targetURL)
	if failure != nil {
		return nil, failure
	}
	effects["filtered_count"] = filteredCount
	effects["security_filtered_count"] = securityFilteredCount
	effects["hybrid"] = hybrid
	effects["truncated"] = truncated
	effects["gone_detection_allowed"] = !hybrid && !truncated && filteredCount == 0 && securityFilteredCount == 0
	if len(sitemaps) > 0 {
		effects["new_sitemap_url"] = sitemaps[0]
	}
	metadataPresent := false
	for _, metadata := range metadataSequence {
		metadataPresent = metadataPresent || metadata != nil
	}
	if metadataPresent {
		metadataValue := any(map[string]any{"batches": metadataSequence})
		if len(metadataSequence) == 1 {
			metadataValue = metadataSequence[0]
		}
		digest, err := semanticsMetadataSHA256(targetURL, metadataValue.(map[string]any))
		if err != nil {
			return nil, semanticsRejected("invalid_projection")
		}
		effects["metadata_updates_sha256"] = digest
	}
	canonicalURLs := make([]string, 0, len(tuples))
	for canonical := range tuples {
		canonicalURLs = append(canonicalURLs, canonical)
	}
	sort.Strings(canonicalURLs)
	for _, canonical := range canonicalURLs {
		tuple := tuples[canonical]
		if tuple.content == nil {
			semanticsAddTarget(effects, canonical, nil)
			continue
		}
		digest, err := semanticsContentSHA256(canonical, tuple.content)
		if err != nil {
			return nil, semanticsRejected("invalid_projection")
		}
		semanticsAddTarget(effects, canonical, &digest)
	}
	return effects, nil
}

func semanticsStopped(caseID string, failure *semanticFailure) ProjectedResult {
	status := "suppressed"
	if failure.rejected {
		status = "rejected"
	}
	return ProjectedResult{"case_id": caseID, "reason": failure.reason, "status": status}
}

// ProjectSemantics derives a closed candidate projection from one safe fixture case.
func ProjectSemantics(caseValue map[string]any) ProjectedResult {
	caseID, ok := caseValue["id"].(string)
	if !ok || caseID == "" {
		return semanticsStopped("invalid-case", semanticsRejected("invalid_projection"))
	}
	if len(caseValue) != 3 && len(caseValue) != 4 {
		return semanticsStopped(caseID, semanticsRejected("invalid_projection"))
	}
	for key := range caseValue {
		if key != "expected" && key != "id" && key != "input" && key != "subject_kind" {
			return semanticsStopped(caseID, semanticsRejected("invalid_projection"))
		}
	}
	input, failure := semanticsShape(
		caseValue["input"],
		map[string]bool{"preconditions": true},
		map[string]bool{
			"batches": true, "comparison_content": true,
			"comparison_content_sha256": true, "comparison_metadata_updates": true,
			"existing_projected_effects": true, "request": true, "result": true,
		},
	)
	if failure != nil {
		return semanticsStopped(caseID, failure)
	}
	preconditions, failure := semanticsShape(
		input["preconditions"],
		map[string]bool{
			"batches_complete": true, "eligible_for_commit": true,
			"privacy_status": true, "protocol_accepted": true, "terminal_status": true,
		},
		nil,
	)
	if failure != nil {
		return semanticsStopped(caseID, failure)
	}
	privacyStatus, privacyOK := preconditions["privacy_status"].(string)
	if privacyStatus == "rejected" {
		return semanticsStopped(caseID, semanticsFailure("privacy_rejected"))
	}
	if !privacyOK || privacyStatus != "unchanged" && privacyStatus != "transformed" {
		return semanticsStopped(caseID, semanticsRejected("invalid_projection"))
	}
	if preconditions["protocol_accepted"] != true || preconditions["terminal_status"] != "success" ||
		preconditions["eligible_for_commit"] != true || preconditions["batches_complete"] != true {
		return semanticsStopped(caseID, semanticsFailure("ineligible_history"))
	}
	if existing, present := input["existing_projected_effects"]; present {
		if failure = semanticsValidateExistingEffects(existing); failure != nil {
			return semanticsStopped(caseID, failure)
		}
	}
	var effects map[string]any
	subject, _ := caseValue["subject_kind"].(string)
	switch subject {
	case "scrape":
		effects, failure = semanticsProjectScrape(input)
	case "monitor":
		effects, failure = semanticsProjectMonitor(input)
	case "browser":
		_, failure = semanticsShape(
			input["result"],
			map[string]bool{"browser_result": true},
			nil,
		)
		if failure == nil {
			failure = semanticsFailure("unsupported_result")
		}
	default:
		failure = semanticsRejected("unsupported_result")
	}
	if failure != nil {
		return semanticsStopped(caseID, failure)
	}
	result := ProjectedResult{
		"case_id":           caseID,
		"projected_effects": effects,
		"status":            "projected",
	}
	digest, err := semanticsResultSHA256(map[string]any(result))
	if err != nil {
		return semanticsStopped(caseID, semanticsRejected("invalid_projection"))
	}
	result["semantic_sha256"] = digest
	return result
}
