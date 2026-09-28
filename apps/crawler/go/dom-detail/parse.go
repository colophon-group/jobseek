package dom

import (
	"bytes"
	_ "embed"
	"encoding/json"
	"errors"
	"fmt"
	"strings"

	"github.com/andybalholm/cascadia"
	"golang.org/x/net/html"
)

func object(v any) (Object, error) {
	m, ok := v.(map[string]any)
	if !ok {
		return nil, errors.New("configuration option must be an object")
	}
	return m, nil
}
func flag(config Object, key string) (bool, error) {
	v, exists := config[key]
	if !exists {
		return false, nil
	}
	b, ok := v.(bool)
	if !ok {
		return false, fmt.Errorf("%s must be a boolean", key)
	}
	return b, nil
}
func scopeHTML(source string, config Object) (string, error) {
	title, err := flag(config, "include_document_title")
	if err != nil {
		return "", err
	}
	description, err := flag(config, "include_document_description")
	if err != nil {
		return "", err
	}
	if config["scope"] == nil {
		if title || description {
			return "", errors.New("document metadata options require scope")
		}
		return source, nil
	}
	scope, ok := config["scope"].(string)
	if !ok || trim(scope) == "" || len([]rune(scope)) > 256 || strings.ContainsRune(scope, 0) {
		return "", errors.New("scope must be a non-empty CSS selector up to 256 chars")
	}
	selector, err := cascadia.Parse(scope)
	if err != nil {
		return "", errors.New("scope is not a valid CSS selector")
	}
	document, err := html.ParseWithOptions(strings.NewReader(source), html.ParseOptionEnableScripting(false))
	if err != nil {
		return "", err
	}
	node := cascadia.Query(document, selector)
	if node == nil {
		return "", errors.New("scope did not match the page")
	}
	var out bytes.Buffer
	if title {
		if n := cascadia.Query(document, cascadia.MustCompile("title")); n != nil {
			if err := renderHTML(&out, n); err != nil {
				return "", err
			}
		}
	}
	if description {
		if n := cascadia.Query(document, cascadia.MustCompile(`meta[name="description"]`)); n != nil {
			for _, a := range n.Attr {
				if a.Key == "content" && a.Val != "" {
					out.WriteString(`<p data-document-description="true">` + htmlEscape.Replace(a.Val) + "</p>")
					break
				}
			}
		}
		out.WriteString(`<p data-document-scope-start="true">scope</p>`)
	}
	if node.Data == "noscript" {
		for child := node.FirstChild; child != nil; child = child.NextSibling {
			if err := renderHTML(&out, child); err != nil {
				return "", err
			}
		}
	} else {
		if err := renderHTML(&out, node); err != nil {
			return "", err
		}
	}
	return out.String(), nil
}

//go:embed presets.json
var presetsJSON []byte
var presets map[string]Object

func init() {
	decoder := json.NewDecoder(bytes.NewReader(presetsJSON))
	decoder.UseNumber()
	if err := decoder.Decode(&presets); err != nil {
		panic(err)
	}
}
func descendantsText(node *html.Node) string {
	var parts []string
	var visit func(*html.Node)
	visit = func(n *html.Node) {
		if n.Type == html.TextNode {
			if value := trim(n.Data); value != "" {
				parts = append(parts, value)
			}
		}
		for child := n.FirstChild; child != nil; child = child.NextSibling {
			visit(child)
		}
	}
	visit(node)
	return strings.Join(parts, " ")
}
func runtimeConfig(source string, config Object) (Object, error) {
	if config["preset"] == nil {
		return config, nil
	}
	if config["preset"] != "elementor-careers" {
		return nil, errors.New("unknown DOM scraper preset")
	}
	document, err := html.ParseWithOptions(strings.NewReader(source), html.ParseOptionEnableScripting(false))
	if err != nil {
		return nil, err
	}
	kind := ""
	if node := cascadia.Query(document, cascadia.MustCompile(`div[data-elementor-type="single-post"]`)); node != nil {
		careerClass := false
		for _, a := range node.Attr {
			if a.Key == "class" {
				for _, class := range strings.FieldsFunc(a.Val, pySpace) {
					careerClass = careerClass || class == "type-careers" || class == "category-careers" || class == "tag-careers"
				}
			}
		}
		if careerClass && cascadia.Query(node, cascadia.MustCompile("h1.elementor-heading-title")) != nil && cascadia.Query(node, cascadia.MustCompile(".elementor-widget-theme-post-content")) != nil {
			kind = "cmsmasters"
		}
	}
	if kind == "" {
		if node := cascadia.Query(document, cascadia.MustCompile(`div[data-elementor-type="wp-post"]`)); node != nil && cascadia.Query(document, cascadia.MustCompile("title")) != nil && cascadia.Query(node, cascadia.MustCompile(".elementor-widget-form")) != nil {
			text := norm(descendantsText(node))
			for _, marker := range []string{"job title:", "currently hiring", "what you'll do"} {
				if strings.Contains(text, marker) {
					kind = "elementor"
					break
				}
			}
		}
	}
	if kind == "" {
		return nil, errors.New("Elementor careers preset did not match the detail page")
	}
	merged := Object{}
	for k, v := range presets[kind] {
		merged[k] = v
	}
	for k, v := range config {
		merged[k] = v
	}
	return merged, nil
}

func applyDefaults(raw Object, config Object, sourceURL *string) (Object, error) {
	defaults := Object{}
	if config["defaults"] != nil {
		m, err := object(config["defaults"])
		if err != nil {
			return nil, err
		}
		for k, v := range m {
			defaults[k] = v
		}
	}
	conditional := Object{}
	cache := regexCache{}
	if config["defaults_by_regex"] != nil {
		rules, ok := config["defaults_by_regex"].([]any)
		if !ok || len(rules) < 1 || len(rules) > 20 {
			return nil, errors.New("defaults_by_regex must contain 1-20 rules")
		}
		matched := false
		for _, v := range rules {
			rule, err := object(v)
			if err != nil || len(rule) != 3 {
				return nil, errors.New("defaults_by_regex rules require field, pattern, and defaults")
			}
			field, ok := rule["field"].(string)
			if !ok || field == "" || len([]rune(field)) > 128 {
				return nil, errors.New("defaults_by_regex field must be a short string")
			}
			pattern, ok := rule["pattern"].(string)
			if !ok || pattern == "" || len([]rune(pattern)) > 512 || strings.ContainsRune(pattern, 0) {
				return nil, errors.New("defaults_by_regex pattern must be 1-512 characters")
			}
			values, err := object(rule["defaults"])
			if err != nil || len(values) == 0 {
				return nil, errors.New("defaults_by_regex defaults must be a non-empty object")
			}
			re, err := cache.compile(pattern, false)
			if err != nil {
				return nil, err
			}
			if text, ok := raw[field].(string); ok && !matched {
				yes, err := re.MatchString(text)
				if err != nil {
					return nil, err
				}
				if yes {
					conditional = values
					matched = true
				}
			}
		}
	}
	for k, v := range conditional {
		defaults[k] = v
	}
	if config["defaults_by_url"] != nil {
		byURL, err := object(config["defaults_by_url"])
		if err != nil {
			return nil, err
		}
		for _, v := range byURL {
			if _, err := object(v); err != nil {
				return nil, errors.New("defaults_by_url must map URL strings to objects")
			}
		}
		if sourceURL != nil {
			if values, ok := byURL[*sourceURL]; ok {
				m, _ := object(values)
				for k, v := range m {
					defaults[k] = v
				}
			}
		}
	}
	merged := Object{}
	for k, v := range raw {
		merged[k] = v
	}
	for k, v := range defaults {
		old := merged[k]
		empty := old == nil
		switch x := old.(type) {
		case string:
			empty = x == ""
		case []any:
			empty = len(x) == 0
		case []string:
			empty = len(x) == 0
		}
		if empty {
			merged[k] = v
		}
	}
	return merged, nil
}
func jobContent(raw Object) Object {
	content := Object{"title": nil, "description": nil, "locations": nil, "employment_type": nil, "job_location_type": nil, "date_posted": nil, "base_salary": nil, "language": nil, "extras": nil, "metadata": nil}
	metadata := Object{}
	extras := Object{}
	for key, value := range raw {
		if value == nil {
			continue
		}
		switch {
		case strings.HasPrefix(key, "metadata."):
			metadata[strings.TrimPrefix(key, "metadata.")] = value
		case key == "title" || key == "description" || key == "employment_type" || key == "job_location_type" || key == "date_posted":
			content[key] = value
		case key == "location" || key == "locations":
			if text, ok := value.(string); ok {
				content["locations"] = []string{text}
			} else {
				content["locations"] = value
			}
		case key == "qualifications" || key == "responsibilities" || key == "skills":
			if text, ok := value.(string); ok {
				extras[key] = []string{text}
			} else {
				extras[key] = value
			}
		case key == "valid_through":
			extras[key] = value
		default:
			metadata[key] = value
		}
	}
	if len(metadata) > 0 {
		content["metadata"] = metadata
	}
	if len(extras) > 0 {
		content["extras"] = extras
	}
	return content
}
func Parse(source string, config Object, sourceURL *string) (Object, error) {
	config, err := runtimeConfig(source, config)
	if err != nil {
		return nil, err
	}
	if !truth(config["steps"]) {
		return jobContent(Object{}), nil
	}
	scoped, err := scopeHTML(source, config)
	if err != nil {
		return nil, err
	}
	header, err := flag(config, "include_header_content")
	if err != nil {
		return nil, err
	}
	elements, err := Flatten(scoped, false, header)
	if err != nil {
		return nil, err
	}
	stepsRaw, ok := config["steps"].([]any)
	if !ok {
		return nil, errors.New("steps must be a list")
	}
	steps := []Object{}
	for _, v := range stepsRaw {
		step, err := object(v)
		if err != nil {
			return nil, err
		}
		steps = append(steps, step)
	}
	start := 0
	if sourceURL != nil {
		_, fragment, _ := strings.Cut(*sourceURL, "#")
		if fragment != "" {
			for i, e := range elements {
				if e.Attrs["id"] == fragment {
					start = i
					break
				}
			}
		}
	}
	raw, _, err := WalkSteps(elements, steps, start)
	if err != nil {
		return nil, err
	}
	raw, err = applyDefaults(raw, config, sourceURL)
	if err != nil {
		return nil, err
	}
	return jobContent(raw), nil
}
