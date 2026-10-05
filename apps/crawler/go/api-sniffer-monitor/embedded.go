package apisniffer

// Embedded detail extraction shares the configured JSON field semantics used by
// the API monitor. This package never fetches pages or grants queue authority.
import (
	"encoding/json"
	"errors"
	"io"
	"reflect"
	"regexp"
	"strings"
	"unicode/utf8"

	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
	"golang.org/x/net/html"
)

var ErrEmbedded = errors.New("invalid configured embedded detail")

func embeddedOptions(config map[string]any, nextdata bool) map[string]any {
	c := make(map[string]any, len(config)+1)
	for k, v := range config {
		c[k] = v
	}
	if nextdata && (c["source"] == nil || c["source"] == "") {
		c["script_id"] = "__NEXT_DATA__"
	}
	return c
}

func ValidateEmbeddedDetail(config map[string]any, nextdata bool) error {
	c := embeddedOptions(config, nextdata)
	for k, v := range c {
		switch k {
		case "path", "script_id", "variable", "pattern", "source":
			if v != nil {
				s, ok := v.(string)
				if !ok || !utf8.ValidString(s) || len(s) > 4096 || strings.ContainsRune(s, 0) {
					return ErrEmbedded
				}
			}
		case "fields", "defaults":
			if v != nil {
				if _, ok := v.(map[string]any); !ok {
					return ErrEmbedded
				}
			}
		case "enrich":
			if v != nil {
				list, ok := v.([]any)
				if !ok {
					return ErrEmbedded
				}
				seen := map[string]bool{}
				for _, f := range list {
					name, ok := f.(string)
					if !ok || (name != "description" && name != "employment_type") || seen[name] {
						return ErrEmbedded
					}
					seen[name] = true
				}
			}
		case "render", "proxy", "skip_ssl":
			if v != nil && v != false {
				return ErrEmbedded
			}
		case "ssl_verify":
			if v != nil && v != true {
				return ErrEmbedded
			}
		default:
			return ErrEmbedded
		}
	}
	if source := c["source"]; source != nil && source != "" && source != "reactrouter" && source != "rsc" {
		return ErrEmbedded
	}
	if p, _ := c["path"].(string); p != "" {
		if _, err := Search(map[string]any{}, p); err != nil {
			return ErrEmbedded
		}
	}
	if p, _ := c["pattern"].(string); p != "" {
		if _, err := dom.CompileURLPattern(p); err != nil {
			return ErrEmbedded
		}
	}
	fields, ok := c["fields"].(map[string]any)
	if !ok || len(fields) == 0 || len(fields) > 128 {
		return ErrEmbedded
	}
	for target, spec := range fields {
		if target == "" || len(target) > 256 || !utf8.ValidString(target) || strings.ContainsRune(target, 0) || ValidateField(spec) != nil {
			return ErrEmbedded
		}
	}
	for k := range asEmbeddedObject(c["defaults"]) {
		switch k {
		case "title", "description", "locations", "employment_type", "job_location_type", "date_posted", "base_salary", "language", "metadata", "extras":
		default:
			return ErrEmbedded
		}
	}
	if !embeddedTruthy(c["source"]) && !embeddedTruthy(c["script_id"]) && !embeddedTruthy(c["pattern"]) && !embeddedTruthy(c["variable"]) {
		return ErrEmbedded
	}
	return nil
}

func asEmbeddedObject(v any) map[string]any { m, _ := v.(map[string]any); return m }
func embeddedTruthy(v any) bool             { s, ok := v.(string); return ok && s != "" }

var embeddedTrailingComma = regexp.MustCompile(`,\s*([}\]])`)

func embeddedDecode(text string) (*Document, error) {
	d, err := Decode([]byte(text))
	if err == nil {
		return d, nil
	}
	return Decode([]byte(embeddedTrailingComma.ReplaceAllString(text, "$1")))
}

// Match Python's bracket/string-aware extent, preserving numbers before field
// conversion. Mixed inner arrays/objects are validated by the JSON decoder.
func embeddedAfter(text string) (*Document, error) {
	start := strings.IndexAny(text, "{[")
	if start < 0 {
		return nil, ErrEmbedded
	}
	opener := text[start]
	closer := byte('}')
	if opener == '[' {
		closer = ']'
	}
	depth := 0
	quoted, escaped := false, false
	for n := start; n < len(text); n++ {
		c := text[n]
		if escaped {
			escaped = false
			continue
		}
		if c == '\\' && quoted {
			escaped = true
			continue
		}
		if c == '"' {
			quoted = !quoted
			continue
		}
		if quoted {
			continue
		}
		if c == opener {
			depth++
		}
		if c == closer {
			depth--
			if depth == 0 {
				return embeddedDecode(text[start : n+1])
			}
		}
	}
	return nil, ErrEmbedded
}

var reactRouterEmbedded = regexp.MustCompile(`window\.__staticRouterHydrationData\s*=\s*JSON\.parse\("(.+?)"\);`)
var rscEmbedded = regexp.MustCompile(`self\.__next_f\.push\(\[1,"((?:[^"\\]|\\.)*)"\]\)`)

func embeddedDocument(source string, c map[string]any) (*Document, error) {
	if c["source"] == "reactrouter" {
		m := reactRouterEmbedded.FindStringSubmatch(source)
		if len(m) != 2 {
			return nil, ErrEmbedded
		}
		var raw string
		if json.Unmarshal([]byte(`"`+m[1]+`"`), &raw) != nil {
			return nil, ErrEmbedded
		}
		return Decode([]byte(raw))
	}
	if c["source"] == "rsc" {
		merged := map[string]any{}
		d := &Document{order: map[reflect.Value][]string{}}
		for _, m := range rscEmbedded.FindAllStringSubmatch(source, -1) {
			var chunk string
			if json.Unmarshal([]byte(`"`+m[1]+`"`), &chunk) != nil {
				continue
			}
			for _, line := range strings.Split(chunk, "\n") {
				line = strings.TrimSpace(line)
				at := strings.IndexByte(line, ':')
				if at < 1 {
					continue
				}
				part, err := Decode([]byte(line[at+1:]))
				if err != nil {
					continue
				}
				v := part.Value
				if a, ok := v.([]any); ok {
					if len(a) < 4 {
						continue
					}
					v = a[3]
				}
				obj, ok := v.(map[string]any)
				if !ok {
					continue
				}
				for k, keys := range part.order {
					d.order[k] = keys
				}
				for _, k := range part.order[reflect.ValueOf(obj)] {
					if _, exists := merged[k]; !exists {
						d.order[reflect.ValueOf(merged)] = append(d.order[reflect.ValueOf(merged)], k)
					}
					merged[k] = obj[k]
				}
			}
		}
		if len(merged) == 0 {
			return nil, ErrEmbedded
		}
		d.Value = merged
		return d, nil
	}
	if id, _ := c["script_id"].(string); id != "" {
		z := html.NewTokenizer(strings.NewReader(source))
		capturing := false
		var raw *string
		for {
			kind := z.Next()
			if kind == html.ErrorToken {
				if z.Err() != io.EOF {
					return nil, ErrEmbedded
				}
				break
			}
			if kind == html.StartTagToken {
				token := z.Token()
				if token.Data == "script" {
					attrs := map[string]string{}
					for _, a := range token.Attr {
						attrs[a.Key] = a.Val
					}
					if attrs["id"] == id {
						capturing = true
					}
				}
			}
			if kind == html.TextToken && capturing {
				v := string(z.Text())
				raw = &v
			}
			if kind == html.EndTagToken && z.Token().Data == "script" {
				capturing = false
			}
		}
		if raw == nil {
			return nil, ErrEmbedded
		}
		return embeddedDecode(strings.TrimSpace(*raw))
	}
	if pattern, _ := c["pattern"].(string); pattern != "" {
		re, err := dom.CompileURLPattern(pattern)
		if err != nil {
			return nil, ErrEmbedded
		}
		m, err := re.FindStringMatch(source)
		if err != nil || m == nil {
			return nil, ErrEmbedded
		}
		index, length := m.ByteRange()
		return embeddedAfter(source[index+length:])
	}
	if variable, _ := c["variable"].(string); variable != "" {
		re := regexp.MustCompile(`(?:var|let|const)\s+` + regexp.QuoteMeta(variable) + `\s*=\s*|` + regexp.QuoteMeta(variable) + `\s*=\s*`)
		matches := re.FindAllStringIndex(source, -1)
		for n := len(matches) - 1; n >= 0; n-- {
			if d, err := embeddedAfter(source[matches[n][1]:]); err == nil {
				return d, nil
			}
		}
	}
	return nil, ErrEmbedded
}

func ProjectEmbeddedDetail(source string, config map[string]any, nextdata bool) (map[string]any, error) {
	if len(source) > 16<<20 || !utf8.ValidString(source) || ValidateEmbeddedDetail(config, nextdata) != nil {
		return nil, ErrEmbedded
	}
	c := embeddedOptions(config, nextdata)
	d, err := embeddedDocument(source, c)
	if err != nil {
		return nil, err
	}
	path, _ := c["path"].(string)
	item, err := Search(d.Value, path)
	if err != nil || item == nil {
		return nil, ErrEmbedded
	}
	values, metadata, extras := map[string]any{}, map[string]any{}, map[string]any{}
	// Python's embedded caller does not supply a root to sibling-table lookup.
	d.Root = map[string]any{}
	for target, spec := range asEmbeddedObject(c["fields"]) {
		value, err := d.Field(item, spec)
		if err != nil {
			return nil, err
		}
		if value == nil {
			continue
		}
		switch target {
		case "title", "description", "employment_type", "job_location_type", "date_posted", "base_salary":
			values[target] = value
		case "locations", "location":
			if _, ok := value.(string); ok {
				value = []any{value}
			}
			values["locations"] = value
		case "qualifications", "responsibilities", "skills":
			if _, ok := value.(string); ok {
				value = []any{value}
			}
			extras[target] = value
		case "valid_through":
			extras[target] = value
		default:
			metadata[strings.TrimPrefix(target, "metadata.")] = value
		}
	}
	if len(metadata) > 0 {
		values["metadata"] = metadata
	}
	if len(extras) > 0 {
		values["extras"] = extras
	}
	// Generic scrape processing applies defaults after structured extras have
	// enriched the raw description. The pure parser retains the original values.
	return values, nil
}
