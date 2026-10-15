package dom

import (
	"context"
	"errors"
	"github.com/andybalholm/cascadia"
	jsonld "github.com/colophon-group/jobseek/apps/crawler/go/jsonld-detail"
	"golang.org/x/net/html"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"unicode/utf8"
)

var ErrLinkedDescription = errors.New("invalid linked DOM description")

type LinkedDescriptionOptions struct {
	Selector        string
	AllowedSuffixes []string
	MinChars        int
	ParseConfig     Object
}

func LinkedDescriptionConfig(value any) (*LinkedDescriptionOptions, error) {
	if value == nil {
		return nil, nil
	}
	c, ok := value.(map[string]any)
	if !ok {
		return nil, ErrLinkedDescription
	}
	allowed := map[string]bool{"selector": true, "allowed_host_suffixes": true, "min_chars": true, "scope": true, "steps": true, "include_header_content": true}
	for k := range c {
		if !allowed[k] {
			return nil, ErrLinkedDescription
		}
	}
	selector, ok := c["selector"].(string)
	if !ok || selector == "" || utf8.RuneCountInString(selector) > 256 {
		return nil, ErrLinkedDescription
	}
	if _, e := cascadia.Parse(selector); e != nil {
		return nil, ErrLinkedDescription
	}
	suffixes, ok := c["allowed_host_suffixes"].([]any)
	if !ok || len(suffixes) < 1 || len(suffixes) > 4 {
		return nil, ErrLinkedDescription
	}
	o := &LinkedDescriptionOptions{Selector: selector, MinChars: 200, ParseConfig: Object{}}
	for _, v := range suffixes {
		s, ok := v.(string)
		if !ok || !strings.HasPrefix(s, ".") || utf8.RuneCountInString(s) > 253 || strings.Contains(s, "/") {
			return nil, ErrLinkedDescription
		}
		o.AllowedSuffixes = append(o.AllowedSuffixes, strings.ToLower(s))
	}
	if v, exists := c["min_chars"]; exists {
		n, e := number(v, 200)
		if e != nil || n < 50 || n > 5000 {
			return nil, ErrLinkedDescription
		}
		o.MinChars = n
	}
	steps, ok := c["steps"].([]any)
	if !ok || len(steps) == 0 {
		return nil, ErrLinkedDescription
	}
	for _, k := range []string{"scope", "steps", "include_header_content"} {
		if v, exists := c[k]; exists {
			o.ParseConfig[k] = v
		}
	}
	return o, nil
}
func (o *LinkedDescriptionOptions) ValidateTarget(source string) error {
	u, e := url.Parse(source)
	if e != nil || strings.ToLower(u.Scheme) != "https" || u.Hostname() == "" || u.User != nil {
		return ErrLinkedDescription
	}
	if u.Port() != "" {
		p, e := strconv.Atoi(u.Port())
		if e != nil || p < 0 || p > 65535 {
			return ErrLinkedDescription
		}
	}
	host := strings.ToLower(u.Hostname())
	for _, suffix := range o.AllowedSuffixes {
		if host == strings.TrimPrefix(suffix, ".") || strings.HasSuffix(host, suffix) {
			return nil
		}
	}
	return ErrLinkedDescription
}
func (o *LinkedDescriptionOptions) Target(document, source string) (string, error) {
	selector, e := cascadia.Parse(o.Selector)
	if e != nil {
		return "", ErrLinkedDescription
	}
	node, e := html.Parse(strings.NewReader(document))
	if e != nil {
		return "", ErrLinkedDescription
	}
	links := cascadia.QueryAll(node, selector)
	if len(links) != 1 {
		return "", ErrLinkedDescription
	}
	href := ""
	for _, a := range links[0].Attr {
		if a.Key == "href" {
			href = a.Val
			break
		}
	}
	if href == "" {
		return "", ErrLinkedDescription
	}
	base, e := url.Parse(source)
	if e != nil {
		return "", ErrLinkedDescription
	}
	relative, e := url.Parse(href)
	if e != nil {
		return "", ErrLinkedDescription
	}
	target := base.ResolveReference(relative).String()
	if o.ValidateTarget(target) != nil {
		return "", ErrLinkedDescription
	}
	return target, nil
}

func descriptionTextLength(value any) int {
	text, _ := value.(string)
	node, err := html.Parse(strings.NewReader(text))
	if err != nil {
		return 0
	}
	return utf8.RuneCountInString(descendantsText(node))
}
func fillLinkedDescription(ctx context.Context, result jsonld.FetchResult, source, endpoint string, config Object, client *http.Client) (jsonld.FetchResult, error) {
	options, err := LinkedDescriptionConfig(config["linked_description"])
	if err != nil {
		return result, err
	}
	if options == nil || descriptionTextLength(result.Content["description"]) >= options.MinChars {
		return result, nil
	}
	target, err := options.Target(source, endpoint)
	if err != nil {
		return result, err
	}
	doc, err := jsonld.FetchLinkedDocumentWithClient(ctx, target, options.ValidateTarget, client)
	result.Requests += doc.Requests
	result.Responses += doc.Responses
	result.Bytes += doc.Bytes
	if err != nil {
		result.Status = doc.Status
		result.FinalURL = doc.FinalURL
		result.ErrorKind = doc.ErrorKind
		result.TDMSource = doc.TDMSource
		result.TDMPolicy = doc.TDMPolicy
		return result, err
	}
	linked, err := Parse(jsonld.DecodeDocument(doc.Body, doc.ContentType), options.ParseConfig, nil)
	if err != nil {
		return result, err
	}
	if descriptionTextLength(linked["description"]) < options.MinChars {
		return result, ErrLinkedDescription
	}
	result.Content["description"] = linked["description"]
	return result, nil
}
