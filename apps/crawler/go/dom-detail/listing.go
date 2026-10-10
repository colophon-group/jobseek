package dom

import (
	"encoding/json"
	"errors"
	stdhtml "html"
	"io"
	"net/url"
	"strings"

	"github.com/andybalholm/cascadia"
	jsonld "github.com/colophon-group/jobseek/apps/crawler/go/jsonld-detail"
	"golang.org/x/net/html"
)

type ListingConfig struct {
	Document                             jsonld.DocumentOptions
	Selector, Include, Exclude, Encoding string
	Attempts                             int
	IncludeBoardURL, RequireJSONLD       bool
	Pagination                           *ListingPagination
	RichRows                             *RichRowsConfig
	ScriptLinks                          *ScriptLinksConfig
	OnclickSelector                      string
	EmptySelector, EmptyText             string
	FetchURL                             string
	BoardURL                             string
	Proofs                               *ListingProofs
	ProviderProof                        *ListingProviderProof
	JoinProofURL                         func(string, string) (string, error)
}

// ListingOptions covers the existing static single-page href inventory.
// Pagination, rendered/proxy routes and content-bearing row contracts remain
// with their current owner until their full inventory semantics are supported.
func ListingOptions(config Object, endpoint string) (ListingConfig, error) {
	c := ListingConfig{Attempts: 3, BoardURL: endpoint}
	allowed := map[string]bool{}
	allowed["rich_rows"] = true
	for _, key := range ListingProviderKeys {
		allowed[key] = true
	}
	for _, key := range []string{"onclick_selector", "script_json_links", "include_board_url", "require_jsonld_jobposting", "advertised_total", "empty_states", "empty_selector", "empty_text", "url_filter", "link_selector", "render", "proxy", "skip_ssl", "ssl_verify", "actions", "pagination", "transport_attempts", "request_headers", "encoding", "wait", "timeout", "headless", "channel", "stealth", "persistent_context", "user_agent", "wait_fallback", "resource_policy", "url_transform"} {
		allowed[key] = true
	}
	for key := range config {
		if !allowed[key] {
			return c, errors.New("unsupported static DOM listing option")
		}
	}
	var providerErr error
	c.ProviderProof, providerErr = listingProviderOptions(config)
	if providerErr != nil {
		return c, providerErr
	}
	if value := config["rich_rows"]; value != nil {
		raw, ok := value.(json.RawMessage)
		if !ok {
			return c, ErrRichRows
		}
		c.RichRows, providerErr = RichRowsOptions(raw)
		if providerErr != nil {
			return c, providerErr
		}
	}
	for key, target := range map[string]*bool{"include_board_url": &c.IncludeBoardURL, "require_jsonld_jobposting": &c.RequireJSONLD} {
		if value, present := config[key]; present {
			flag, ok := value.(bool)
			if !ok {
				return c, errors.New("invalid DOM listing verification flag")
			}
			*target = flag
		}
	}
	for _, key := range []string{"render", "proxy", "skip_ssl", "actions"} {
		if truth(config[key]) {
			return c, errors.New("unsupported static DOM listing route")
		}
	}
	if value, ok := config["ssl_verify"]; ok && value != nil && value != true {
		return c, errors.New("DOM listing requires verified TLS")
	}
	if value := config["transport_attempts"]; value != nil {
		if _, ok := value.(bool); ok {
			return c, errors.New("invalid DOM listing attempt budget")
		}
		var err error
		c.Attempts, err = number(value, 3)
		if err != nil || c.Attempts < 1 || c.Attempts > 5 {
			return c, errors.New("invalid DOM listing attempt budget")
		}
	}
	if value := config["link_selector"]; value != nil {
		s, ok := value.(string)
		if !ok || strings.TrimSpace(s) == "" || len([]rune(s)) > 256 || strings.ContainsRune(s, 0) {
			return c, errors.New("invalid DOM listing selector")
		}
		c.Selector = strings.TrimSpace(s)
		if _, err := cascadia.ParseGroup(c.Selector); err != nil {
			return c, err
		}
	}
	if value := config["url_filter"]; value != nil && truth(value) {
		if s, ok := value.(string); ok {
			c.Include = s
		} else {
			m, err := object(value)
			if err != nil {
				return c, err
			}
			for key, value := range m {
				if key != "include" && key != "exclude" {
					return c, errors.New("invalid DOM listing filter")
				}
				if value == nil {
					continue
				}
				s, ok := value.(string)
				if !ok {
					return c, errors.New("invalid DOM listing pattern")
				}
				if key == "include" {
					c.Include = s
				} else {
					c.Exclude = s
				}
			}
		}
	}
	for _, pattern := range []string{c.Include, c.Exclude} {
		if _, err := CompileURLPattern(pattern); err != nil {
			return c, err
		}
	}
	if value := config["encoding"]; value != nil {
		s, ok := value.(string)
		if !ok || !directEncoding(s) {
			return c, errors.New("unsupported DOM listing encoding")
		}
		c.Encoding = strings.ToLower(strings.ReplaceAll(s, "_", "-"))
		if c.Encoding == "cp1252" {
			c.Encoding = "windows-1252"
		}
	}
	var err error
	c.ScriptLinks, err = ScriptLinksOptions(config["script_json_links"])
	if err != nil {
		return c, err
	}
	c.OnclickSelector, err = proofSelector(config["onclick_selector"], false)
	if err != nil {
		return c, err
	}
	if c.ScriptLinks != nil && (c.OnclickSelector != "" || c.Selector != "" || config["pagination"] != nil || config["empty_states"] != nil || config["empty_selector"] != nil || config["empty_text"] != nil || config["advertised_total"] != nil || c.IncludeBoardURL) {
		return c, errScriptListing
	}
	if c.OnclickSelector != "" && (config["pagination"] != nil || c.IncludeBoardURL) {
		return c, errScriptListing
	}
	c.Pagination, err = listingPagination(config["pagination"], endpoint)
	if err != nil {
		return c, err
	}
	if err = listingEmptyOptions(config, &c); err != nil {
		return c, err
	}
	c.Proofs, err = ListingProofOptions(config)
	if err != nil || c.Proofs != nil && c.Pagination != nil && (c.Proofs.TotalPattern == nil || len(c.Proofs.EmptyStates) != 0) {
		return c, ErrListingProof
	}
	if (c.IncludeBoardURL || c.RequireJSONLD) && (c.EmptySelector != "" || config["empty_states"] != nil) || c.IncludeBoardURL && config["advertised_total"] != nil {
		return c, ErrListingProof
	}
	c.Document, err = directDocumentOptions(config, endpoint)
	// The listing outer loop owns one shared attempt budget across statuses,
	// empty documents and transport failures, including public-header requests.
	c.Document.RetryLimits = nil
	if err == nil && c.Pagination != nil && c.Pagination.MaxPages >= 2 && c.Document.PublicHeaders {
		initial, _ := url.Parse(endpoint)
		for _, page := range []int{2, c.Pagination.MaxPages} {
			next, _ := url.Parse(c.Pagination.URL(endpoint, page))
			if next == nil || initial.Scheme != next.Scheme || initial.Host != next.Host {
				return c, errors.New("public-header pagination requires the board origin")
			}
		}
	}
	return c, err
}

func ListingHrefs(source, selector string) ([]string, error) {
	result := []string{}
	if selector != "" {
		s, err := cascadia.ParseGroup(selector)
		if err != nil {
			return nil, err
		}
		tree, err := html.ParseWithOptions(strings.NewReader(source), html.ParseOptionEnableScripting(false))
		if err != nil {
			return nil, err
		}
		for _, node := range cascadia.QueryAll(tree, s) {
			for _, attr := range node.Attr {
				if attr.Key == "href" && attr.Val != "" {
					result = append(result, attr.Val)
					break
				}
			}
		}
		return result, nil
	}
	z := html.NewTokenizer(strings.NewReader(source))
	for {
		kind := z.Next()
		if kind == html.ErrorToken {
			if err := z.Err(); err != io.EOF {
				return nil, err
			}
			return result, nil
		}
		if kind != html.StartTagToken && kind != html.SelfClosingTagToken {
			continue
		}
		token := z.Token()
		if token.Data != "a" {
			continue
		}
		// HTMLParser preserves duplicate href attributes; the HTML5 tokenizer
		// intentionally keeps only the first. Read this start tag's raw attrs so
		// migration cannot silently omit the second discovered posting.
		result = append(result, rawListingHrefs(string(z.Raw()))...)
	}
}

func rawListingHrefs(tag string) []string {
	out := []string{}
	space := func(c byte) bool { return c == ' ' || c == '\t' || c == '\r' || c == '\n' || c == '\f' }
	i := 1
	for i < len(tag) && !space(tag[i]) && tag[i] != '>' && tag[i] != '/' {
		i++
	}
	for i < len(tag) {
		for i < len(tag) && space(tag[i]) {
			i++
		}
		if i >= len(tag) || tag[i] == '>' || tag[i] == '/' && i+1 < len(tag) && tag[i+1] == '>' {
			break
		}
		start := i
		for i < len(tag) && !space(tag[i]) && tag[i] != '=' && tag[i] != '>' {
			i++
		}
		key := strings.ToLower(tag[start:i])
		for i < len(tag) && space(tag[i]) {
			i++
		}
		if i >= len(tag) || tag[i] != '=' {
			continue
		}
		i++
		for i < len(tag) && space(tag[i]) {
			i++
		}
		if i >= len(tag) {
			break
		}
		var value string
		if tag[i] == '\'' || tag[i] == '"' {
			quote := tag[i]
			i++
			start = i
			for i < len(tag) && tag[i] != quote {
				i++
			}
			value = tag[start:i]
			if i < len(tag) {
				i++
			}
		} else {
			start = i
			for i < len(tag) && !space(tag[i]) && tag[i] != '>' {
				i++
			}
			value = tag[start:i]
		}
		if key == "href" && value != "" {
			out = append(out, stdhtml.UnescapeString(value))
		}
	}
	return out
}
