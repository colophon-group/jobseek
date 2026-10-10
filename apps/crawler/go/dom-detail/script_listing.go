package dom

import (
	"context"
	"encoding/json"
	"errors"
	"html"
	"net/url"
	"regexp"
	"strings"
	"unicode"
	"unicode/utf8"

	"github.com/andybalholm/cascadia"
	xhtml "golang.org/x/net/html"
)

var errScriptListing = errors.New("invalid inline DOM inventory")
var scriptIdentifier = regexp.MustCompile(`^[A-Za-z_$][A-Za-z0-9_$]*$`)

const scriptSpace = `[[:space:]\p{Z}\x{0085}\x{001c}-\x{001f}]`

var onclickLocation = regexp.MustCompile(`(?i)^` + scriptSpace + `*(?:javascript:` + scriptSpace + `*)?window\.location(?:\.href)?` + scriptSpace + `*=` + scriptSpace + `*(['"])([^'"]+)(['"])` + scriptSpace + `*;?` + scriptSpace + `*$`)

type ScriptLinksConfig struct {
	Variable, Function                                string
	Argument                                          int
	URLField, URLTemplate, TitleField, LocationsField string
	HTMLUnescape                                      bool
}

func (c *ScriptLinksConfig) Rich() bool { return c != nil && c.TitleField != "" }
func scriptText(v any, max int, required bool) (string, error) {
	if v == nil && !required {
		return "", nil
	}
	s, ok := v.(string)
	if !ok || utf8.RuneCountInString(s) > max || strings.ContainsRune(s, 0) || required && s == "" {
		return "", errScriptListing
	}
	return s, nil
}
func ScriptLinksOptions(raw any) (*ScriptLinksConfig, error) {
	if raw == nil {
		return nil, nil
	}
	m, e := object(raw)
	if e != nil {
		return nil, e
	}
	allowed := map[string]bool{"variable": true, "function": true, "argument_index": true, "url_field": true, "url_template": true, "title_field": true, "locations_field": true, "html_unescape": true}
	for k := range m {
		if !allowed[k] {
			return nil, errScriptListing
		}
	}
	c := &ScriptLinksConfig{Argument: -1}
	if c.Variable, e = scriptText(m["variable"], 128, false); e != nil {
		return nil, e
	}
	if c.Function, e = scriptText(m["function"], 128, false); e != nil {
		return nil, e
	}
	variable := m["variable"] != nil
	function := m["function"] != nil || m["argument_index"] != nil
	if variable == function {
		return nil, errScriptListing
	}
	if variable {
		if !scriptIdentifier.MatchString(c.Variable) {
			return nil, errScriptListing
		}
	} else {
		if !scriptIdentifier.MatchString(c.Function) {
			return nil, errScriptListing
		}
		if _, ok := m["argument_index"].(bool); ok {
			return nil, errScriptListing
		}
		if c.Argument, e = number(m["argument_index"], -1); e != nil || c.Argument < 0 || c.Argument > 15 {
			return nil, errScriptListing
		}
	}
	if c.URLField, e = scriptText(m["url_field"], 128, true); e != nil {
		return nil, e
	}
	if c.URLTemplate, e = scriptText(m["url_template"], 2048, true); e != nil || strings.Count(c.URLTemplate, "{value}") != 1 {
		return nil, errScriptListing
	}
	if c.URLTemplate != "{value}" && !scriptAbsoluteURL(strings.Replace(c.URLTemplate, "{value}", "placeholder", 1)) {
		return nil, errScriptListing
	}
	if c.TitleField, e = scriptText(m["title_field"], 128, false); e != nil {
		return nil, e
	}
	if c.LocationsField, e = scriptText(m["locations_field"], 128, false); e != nil {
		return nil, e
	}
	if (m["title_field"] != nil) != (m["locations_field"] != nil) || m["title_field"] != nil && (c.TitleField == "" || c.LocationsField == "") {
		return nil, errScriptListing
	}
	if v, ok := m["html_unescape"]; ok {
		if c.HTMLUnescape, ok = v.(bool); !ok {
			return nil, errScriptListing
		}
	}
	return c, nil
}
func scriptAbsoluteURL(raw string) bool {
	u, e := url.Parse(raw)
	return e == nil && (u.Scheme == "http" || u.Scheme == "https") && u.Host != ""
}
func scriptWhitespace(r rune) bool { return unicode.IsSpace(r) || r >= 0x1c && r <= 0x1f }
func ScriptJSONPayload(source string, c *ScriptLinksConfig) (string, error) {
	if c == nil {
		return "", errScriptListing
	}
	if c.HTMLUnescape {
		source = html.UnescapeString(source)
	}
	if c.Variable != "" {
		pattern := regexp.MustCompile(`(?:const|let|var)` + scriptSpace + `+` + regexp.QuoteMeta(c.Variable) + scriptSpace + `*=` + scriptSpace + `*`)
		matches := pattern.FindAllStringIndex(source, -1)
		if len(matches) != 1 {
			return "", errScriptListing
		}
		return strings.TrimLeftFunc(source[matches[0][1]:], scriptWhitespace), nil
	}
	pattern := regexp.MustCompile(`(?:^|[^A-Za-z0-9_$])` + regexp.QuoteMeta(c.Function) + scriptSpace + `*\(`)
	matches := pattern.FindAllStringIndex(source, -1)
	if len(matches) != 1 {
		return "", errScriptListing
	}
	cursor, argument := matches[0][1], 0
	stack := []byte{}
	var quote byte
	escaped, lineComment, blockComment := false, false, false
	for cursor < len(source) {
		ch := source[cursor]
		var next byte
		if cursor+1 < len(source) {
			next = source[cursor+1]
		}
		if lineComment {
			if ch == '\r' || ch == '\n' {
				lineComment = false
			}
			cursor++
			continue
		}
		if blockComment {
			if ch == '*' && next == '/' {
				blockComment = false
				cursor += 2
			} else {
				cursor++
			}
			continue
		}
		if quote != 0 {
			if escaped {
				escaped = false
			} else if ch == '\\' {
				escaped = true
			} else if ch == quote {
				quote = 0
			}
			cursor++
			continue
		}
		if ch == '/' && next == '/' {
			lineComment = true
			cursor += 2
			continue
		}
		if ch == '/' && next == '*' {
			blockComment = true
			cursor += 2
			continue
		}
		if argument == c.Argument {
			r, n := utf8.DecodeRuneInString(source[cursor:])
			if scriptWhitespace(r) {
				cursor += n
				continue
			}
			return source[cursor:], nil
		}
		if ch == '"' || ch == '\'' || ch == '`' {
			quote = ch
			cursor++
			continue
		}
		if ch == '[' || ch == '{' || ch == '(' {
			stack = append(stack, ch)
			cursor++
			continue
		}
		if ch == ']' || ch == '}' || ch == ')' {
			want := byte('(')
			if ch == ']' {
				want = '['
			}
			if ch == '}' {
				want = '{'
			}
			if len(stack) == 0 || stack[len(stack)-1] != want {
				return "", errScriptListing
			}
			stack = stack[:len(stack)-1]
			cursor++
			continue
		}
		if ch == ',' && len(stack) == 0 {
			argument++
		}
		cursor++
	}
	return "", errScriptListing
}
func ParseScriptLinks(ctx context.Context, source, base string, c *ScriptLinksConfig, include string) ([]RichRowJob, error) {
	payload, e := ScriptJSONPayload(source, c)
	if e != nil {
		return nil, e
	}
	d := json.NewDecoder(strings.NewReader(payload))
	d.UseNumber()
	var raw any
	if d.Decode(&raw) != nil {
		return nil, errScriptListing
	}
	items, ok := raw.([]any)
	if !ok || len(items) > 50000 {
		return nil, errScriptListing
	}
	filter, e := CompileURLPattern(include)
	if e != nil {
		return nil, e
	}
	seen := map[string]bool{}
	jobs := make([]RichRowJob, 0, len(items))
	for _, raw := range items {
		if ctx.Err() != nil {
			return nil, ctx.Err()
		}
		item, ok := raw.(map[string]any)
		if !ok {
			return nil, errScriptListing
		}
		value, e := scriptText(item[c.URLField], 512, true)
		if e != nil {
			return nil, e
		}
		target := strings.Replace(c.URLTemplate, "{value}", value, 1)
		if !scriptAbsoluteURL(target) || !richRowSameOrigin(target, base) || seen[target] {
			return nil, errScriptListing
		}
		if include != "" {
			match, e := filter.MatchString(target)
			if e != nil || !match {
				return nil, errScriptListing
			}
		}
		seen[target] = true
		job := RichRowJob{URL: target}
		if c.Rich() {
			title, e := scriptText(item[c.TitleField], 512, true)
			if e != nil || strings.TrimFunc(title, scriptWhitespace) == "" {
				return nil, errScriptListing
			}
			job.Title = strings.TrimFunc(html.UnescapeString(title), scriptWhitespace)
			var locations []any
			switch v := item[c.LocationsField].(type) {
			case string:
				locations = []any{v}
			case []any:
				if len(v) < 1 || len(v) > 32 {
					return nil, errScriptListing
				}
				locations = v
			default:
				return nil, errScriptListing
			}
			for _, v := range locations {
				s, e := scriptText(v, 512, true)
				if e != nil || strings.TrimFunc(s, scriptWhitespace) == "" {
					return nil, errScriptListing
				}
				s = strings.TrimFunc(html.UnescapeString(s), scriptWhitespace)
				found := false
				for _, known := range job.Locations {
					found = found || known == s
				}
				if !found {
					job.Locations = append(job.Locations, s)
				}
			}
		}
		jobs = append(jobs, job)
	}
	return jobs, nil
}
func ParseOnclickLinks(ctx context.Context, source, base, selector, include string, allowEmpty bool, joinURL func(string, string) (string, error)) ([]string, error) {
	doc, e := xhtml.Parse(strings.NewReader(source))
	if e != nil {
		return nil, e
	}
	sel, e := cascadia.Compile(selector)
	if e != nil {
		return nil, e
	}
	nodes := cascadia.QueryAll(doc, sel)
	if len(nodes) == 0 && !allowEmpty {
		return nil, errScriptListing
	}
	filter, e := CompileURLPattern(include)
	if e != nil {
		return nil, e
	}
	seen := map[string]bool{}
	out := []string{}
	for _, n := range nodes {
		if ctx.Err() != nil {
			return nil, ctx.Err()
		}
		value := ""
		for _, a := range n.Attr {
			if a.Key == "onclick" {
				value = a.Val
				break
			}
		}
		match := onclickLocation.FindStringSubmatch(html.UnescapeString(value))
		if len(match) != 4 || match[1] != match[3] || utf8.RuneCountInString(match[2]) > 2048 {
			return nil, errScriptListing
		}
		href := html.UnescapeString(match[2])
		if joinURL == nil {
			return nil, errScriptListing
		}
		target, e := joinURL(base, href)
		if e != nil {
			return nil, e
		}
		if !scriptAbsoluteURL(target) || !richRowSameOrigin(target, base) || seen[target] {
			return nil, errScriptListing
		}
		if include != "" {
			ok, e := filter.MatchString(target)
			if e != nil || !ok {
				return nil, errScriptListing
			}
		}
		seen[target] = true
		out = append(out, target)
	}
	return out, nil
}
