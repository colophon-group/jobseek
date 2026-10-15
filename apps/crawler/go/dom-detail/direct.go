package dom

import (
	"bytes"
	"context"
	"errors"
	"mime"
	"net/http"
	"strconv"
	"strings"

	jsonld "github.com/colophon-group/jobseek/apps/crawler/go/jsonld-detail"
)

// ValidateDirectConfig admits only the static document and existing pure DOM
// parser, including bounded alternate reads and allowlisted linked descriptions.
func ValidateDirectConfig(config Object) error {
	allowed := map[string]bool{}
	for _, key := range []string{"steps", "preset", "scope", "include_document_title", "include_document_description", "include_header_content", "defaults", "defaults_by_url", "defaults_by_regex", "gone_url_pattern", "retry_statuses", "same_origin_redirects", "request_headers", "encoding", "render", "actions", "proxy", "skip_ssl", "ssl_verify", "enrich", "fallback", "document_fallback", "fetch_url_transform", "linked_description", "_replace", "map"} {
		allowed[key] = true
	}
	for key := range config {
		if !allowed[key] {
			return errors.New("unsupported direct DOM option")
		}
	}
	for _, key := range []string{"render", "actions", "proxy", "skip_ssl", "enrich", "fallback"} {
		if truth(config[key]) {
			return errors.New("unsupported direct DOM pipeline")
		}
	}
	if value, ok := config["ssl_verify"]; ok && value != nil && value != true {
		return errors.New("DOM requires verified TLS")
	}
	for _, key := range []string{"same_origin_redirects", "include_document_title", "include_document_description", "include_header_content"} {
		if _, err := flag(config, key); err != nil {
			return err
		}
	}
	if preset := config["preset"]; preset != nil && preset != "elementor-careers" {
		return errors.New("unknown DOM preset")
	}
	if config["preset"] == nil {
		steps, ok := config["steps"].([]any)
		if !ok || len(steps) == 0 {
			return errors.New("DOM requires configured steps")
		}
		for _, step := range steps {
			if _, err := object(step); err != nil {
				return err
			}
		}
	}
	if value := config["encoding"]; value != nil {
		label, ok := value.(string)
		if !ok || !directEncoding(label) {
			return errors.New("unsupported direct DOM encoding")
		}
	}
	if value := config["document_fallback"]; value != nil && value != false {
		formats, ok := value.(map[string]any)
		if !ok {
			return errors.New("invalid document fallback")
		}
		for kind, raw := range formats {
			if kind != "pdf" && kind != "docx" && kind != "doc" {
				return errors.New("invalid document format")
			}
			if _, ok := raw.(map[string]any); !ok {
				return errors.New("invalid document format options")
			}
		}
	}
	if _, err := LinkedDescriptionConfig(config["linked_description"]); err != nil {
		return err
	}
	_, err := directDocumentOptions(config, "")
	return err
}

func directEncoding(label string) bool {
	switch strings.ToLower(strings.ReplaceAll(label, "_", "-")) {
	case "utf-8", "utf8", "iso-8859-1", "latin-1", "latin1", "windows-1252", "cp1252", "euc-jp":
		return true
	}
	return false
}

func directDocumentOptions(config Object, endpoint string) (jsonld.DocumentOptions, error) {
	opts := jsonld.DocumentOptions{RetryLimits: map[int]int{}}
	if jsonld.IsAvatureDetailURL(endpoint) {
		opts.RetryLimits[406] = 2
	}
	if value := config["retry_statuses"]; value != nil {
		limits, err := object(value)
		if err != nil {
			return opts, err
		}
		for rawStatus, value := range limits {
			status, err := strconv.Atoi(rawStatus)
			limit, limitErr := number(value, 0)
			if err != nil || limitErr != nil || status < 400 || status > 599 || limit < 0 || limit > 5 {
				return opts, errors.New("invalid DOM status retries")
			}
			if limit > opts.RetryLimits[status] {
				opts.RetryLimits[status] = limit
			}
		}
	}
	if value := config["request_headers"]; value != nil {
		headers, err := object(value)
		if err != nil {
			return opts, err
		}
		opts.Headers = map[string]string{}
		for key, value := range headers {
			text, ok := value.(string)
			if !ok {
				return opts, errors.New("invalid DOM public header")
			}
			opts.Headers[key] = text
		}
		opts.PublicHeaders = len(opts.Headers) > 0
	}
	var err error
	opts.SameOrigin, err = flag(config, "same_origin_redirects")
	if err != nil {
		return opts, err
	}
	if err := jsonld.ValidateDocumentOptions(opts); err != nil {
		return opts, err
	}
	// Validate original keys before trimming so duplicate normalized names and
	// the original length/control-character limits cannot disappear in a map.
	headers := map[string]string{}
	for key, value := range opts.Headers {
		headers[strings.TrimSpace(key)] = strings.TrimSpace(value)
	}
	opts.Headers = headers
	return opts, nil
}

// FetchDetailWithClient reuses the public document fetcher's bounded redirects,
// cookies, status retries and publisher checks, followed by the existing parser.
func FetchDetailWithClient(ctx context.Context, endpoint string, config Object, client *http.Client) (jsonld.FetchResult, error) {
	return FetchDetailWithDocumentParser(ctx, endpoint, config, client, nil)
}

// The installed worker supplies its bounded native binary decoders. Publisher
// configuration cannot supply executable code or select a command.
func FetchDetailWithDocumentParser(ctx context.Context, endpoint string, config Object, client *http.Client, binary func(context.Context, []byte, string, map[string]any) (map[string]any, error)) (jsonld.FetchResult, error) {
	result := jsonld.FetchResult{}
	if err := ValidateDirectConfig(config); err != nil {
		return result, err
	}
	fetchURL, err := AlternateFetchURL(endpoint, config["fetch_url_transform"])
	if err != nil {
		return result, err
	}
	opts, err := directDocumentOptions(config, endpoint)
	if err != nil {
		return result, err
	}
	doc, err := jsonld.FetchDocumentWithClient(ctx, fetchURL, opts, client)
	result.Requests, result.Responses, result.Bytes = doc.Requests, doc.Responses, doc.Bytes
	result.Status, result.FinalURL = doc.Status, doc.FinalURL
	result.ErrorKind, result.TDMSource, result.TDMPolicy = doc.ErrorKind, doc.TDMSource, doc.TDMPolicy
	if err != nil {
		return result, err
	}
	contentType := doc.ContentType
	if label, ok := config["encoding"].(string); ok {
		label = strings.ToLower(strings.ReplaceAll(label, "_", "-"))
		if label == "cp1252" {
			label = "windows-1252"
		}
		contentType = mime.FormatMediaType("text/html", map[string]string{"charset": label})
	}
	source := jsonld.DecodeDocument(doc.Body, contentType)
	policy, err := ClassifyDocument("", config, doc.FinalURL)
	if err != nil {
		return result, err
	}
	if policy["classification"] == "gone" {
		result.Status, result.ErrorKind = 410, "status"
		return result, errors.New("DOM redirected to gone URL")
	}
	if doc.Status < 200 || doc.Status >= 300 {
		result.ErrorKind = "status"
		return result, errors.New("DOM document HTTP failure")
	}
	if fallback, ok := config["document_fallback"].(map[string]any); ok && (bytes.HasPrefix(bytes.TrimSpace(doc.Body), []byte("%PDF-")) || bytes.HasPrefix(doc.Body, []byte("PK\x03\x04")) || bytes.HasPrefix(doc.Body, []byte{0xd0, 0xcf, 0x11, 0xe0, 0xa1, 0xb1, 0x1a, 0xe1})) {
		if ctx.Err() != nil {
			return result, ctx.Err()
		}
		if binary == nil {
			return result, errors.New("native binary document decoder is required")
		}
		result.Content, err = binary(ctx, doc.Body, endpoint, fallback)
		return result, err
	}
	policy, err = ClassifyDocument(source, config, doc.FinalURL)
	if err != nil {
		return result, err
	}
	if policy["classification"] == "challenge" {
		return result, errors.New("DOM origin challenge")
	}
	if ctx.Err() != nil {
		return result, ctx.Err()
	}
	result.Content, err = Parse(source, config, &endpoint)
	if err != nil {
		return result, err
	}
	return fillLinkedDescription(ctx, result, source, endpoint, config, client)
}
