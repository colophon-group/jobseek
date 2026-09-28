package join

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"regexp"
	"strings"
	"unicode"

	"golang.org/x/net/html"
)

type DetailRequest struct {
	URL    string                     `json:"url"`
	HTML   string                     `json:"html"`
	Config map[string]json.RawMessage `json:"config"`
}

var detailSpecs = map[string][]string{
	"title":             {"title"},
	"description":       {"description", "schemaDescription", "schemaDescription || unifiedDescription || description"},
	"locations":         {"city.cityName"},
	"employment_type":   {"employmentType.googleType", "employmentType.name"},
	"job_location_type": {"workplaceType"},
	"date_posted":       {"createdAt"},
}

func ValidateDetailConfig(config map[string]json.RawMessage) (map[string]string, error) {
	for key := range config {
		if key != "path" && key != "fields" {
			return nil, errors.New("unsupported JOIN detail configuration")
		}
	}
	var fields map[string]json.RawMessage
	if raw := config["fields"]; len(raw) > 0 && string(raw) != "null" {
		if err := json.Unmarshal(raw, &fields); err != nil {
			return nil, err
		}
	}
	if len(fields) == 0 {
		return map[string]string{}, nil
	}
	var path string
	if json.Unmarshal(config["path"], &path) != nil || path != "props.pageProps.initialState.job" {
		return nil, errors.New("unsupported JOIN detail path")
	}
	result := make(map[string]string)
	for target, raw := range fields {
		var spec string
		if target == "locations" {
			var literal []string
			if json.Unmarshal(raw, &literal) == nil && len(literal) == 1 && literal[0] == "=Switzerland" {
				result[target] = "=Switzerland"
				continue
			}
		}
		if json.Unmarshal(raw, &spec) != nil {
			return nil, errors.New("unsupported JOIN field spec")
		}
		matched := false
		for _, known := range detailSpecs[target] {
			matched = matched || known == spec
		}
		if !matched {
			return nil, errors.New("unsupported JOIN field mapping")
		}
		result[target] = spec
	}
	return result, nil
}

// Preserve Python dict insertion order for str() of unusual provider values.
type orderedObject struct {
	keys   []string
	values map[string]any
}

func decodeValue(d *json.Decoder) (any, error) {
	token, err := d.Token()
	if err != nil {
		return nil, err
	}
	if token == json.Delim('{') {
		o := orderedObject{values: make(map[string]any)}
		for d.More() {
			k, err := d.Token()
			if err != nil {
				return nil, err
			}
			key, ok := k.(string)
			if !ok {
				return nil, errors.New("invalid object key")
			}
			value, err := decodeValue(d)
			if err != nil {
				return nil, err
			}
			if _, exists := o.values[key]; !exists {
				o.keys = append(o.keys, key)
			}
			o.values[key] = value
		}
		_, err = d.Token()
		return o, err
	}
	if token == json.Delim('[') {
		values := []any{}
		for d.More() {
			v, err := decodeValue(d)
			if err != nil {
				return nil, err
			}
			values = append(values, v)
		}
		_, err = d.Token()
		return values, err
	}
	return token, nil
}

func detailPath(root any, path string) any {
	value := root
	for _, part := range strings.Split(path, ".") {
		o, ok := value.(orderedObject)
		if !ok {
			return nil
		}
		value = o.values[part]
	}
	return value
}

func pythonQuote(value string) string {
	quote := byte('\'')
	if strings.Contains(value, "'") && !strings.Contains(value, `"`) {
		quote = '"'
	}
	var b strings.Builder
	b.WriteByte(quote)
	for _, r := range value {
		switch r {
		case '\\':
			b.WriteString(`\\`)
		case '\n':
			b.WriteString(`\n`)
		case '\r':
			b.WriteString(`\r`)
		case '\t':
			b.WriteString(`\t`)
		default:
			if r == rune(quote) {
				b.WriteByte('\\')
				b.WriteRune(r)
			} else if !unicode.IsPrint(r) {
				if r < 256 {
					fmt.Fprintf(&b, `\x%02x`, r)
				} else if r <= 65535 {
					fmt.Fprintf(&b, `\u%04x`, r)
				} else {
					fmt.Fprintf(&b, `\U%08x`, r)
				}
			} else {
				b.WriteRune(r)
			}
		}
	}
	b.WriteByte(quote)
	return b.String()
}

func pythonRepr(value any) string {
	switch v := value.(type) {
	case nil:
		return "None"
	case string:
		return pythonQuote(v)
	case bool:
		if v {
			return "True"
		}
		return "False"
	case json.Number:
		return pythonNumber(v)
	case []any:
		parts := make([]string, len(v))
		for i, x := range v {
			parts[i] = pythonRepr(x)
		}
		return "[" + strings.Join(parts, ", ") + "]"
	case orderedObject:
		parts := []string{}
		for _, key := range v.keys {
			parts = append(parts, pythonQuote(key)+": "+pythonRepr(v.values[key]))
		}
		return "{" + strings.Join(parts, ", ") + "}"
	}
	return ""
}

func detailTruthy(v any) bool {
	switch x := v.(type) {
	case nil:
		return false
	case bool:
		return x
	case string:
		return x != ""
	case []any:
		return len(x) > 0
	case orderedObject:
		return len(x.keys) > 0
	}
	return true // JMESPath numbers, including zero, are truthy.
}

func detailField(root any, spec string) any {
	if spec == "=Switzerland" {
		return "Switzerland"
	}
	var value any
	for _, path := range strings.Split(spec, " || ") {
		value = detailPath(root, path)
		if detailTruthy(value) {
			break
		}
	}
	if value == nil {
		return nil
	}
	if list, ok := value.([]any); ok {
		values := []string{}
		for _, item := range list {
			if item == nil {
				continue
			}
			if s, ok := item.(string); ok {
				values = append(values, s)
			} else {
				values = append(values, pythonRepr(item))
			}
		}
		if len(values) == 0 {
			return nil
		}
		return values
	}
	if s, ok := value.(string); ok {
		return s
	}
	return pythonRepr(value)
}

func emptyContent() map[string]any {
	result := make(map[string]any)
	for _, k := range []string{"title", "description", "locations", "employment_type", "job_location_type", "date_posted", "base_salary", "language", "extras", "metadata"} {
		result[k] = nil
	}
	return result
}

// HTMLParser-compatible script selection: attribute order/case/entities and
// duplicate IDs are accepted; the last completed matching script wins.
func detailScript(body []byte) []byte {
	z := html.NewTokenizer(bytes.NewReader(body))
	capturing := false
	var pending, result []byte
	for {
		switch z.Next() {
		case html.ErrorToken:
			return result
		case html.StartTagToken:
			raw := append([]byte(nil), z.Raw()...)
			t := z.Token()
			if t.Data != "script" {
				continue
			}
			id := pythonAttributes(raw)["id"]
			capturing = id == "__NEXT_DATA__"
			pending = nil
		case html.TextToken:
			if capturing {
				pending = append([]byte(nil), z.Raw()...)
			}
		case html.EndTagToken:
			if z.Token().Data == "script" && capturing {
				if pending != nil {
					result = pending
				}
				capturing = false
			}
		}
	}
}

var trailingCommaRE = regexp.MustCompile(`,\s*([}\]])`)

func ParseDetail(body []byte, config map[string]json.RawMessage) (map[string]any, error) {
	fields, err := ValidateDetailConfig(config)
	if err != nil {
		return nil, err
	}
	content := emptyContent()
	if len(fields) == 0 {
		return content, nil
	}
	script := detailScript(body)
	if script == nil {
		return content, nil
	}
	parse := func(raw []byte) (any, error) {
		d := json.NewDecoder(bytes.NewReader(raw))
		d.UseNumber()
		v, err := decodeValue(d)
		if err != nil {
			return nil, err
		}
		if _, err := d.Token(); err != io.EOF {
			return nil, errors.New("trailing JSON")
		}
		return v, nil
	}
	root, err := parse(script)
	if err != nil {
		root, err = parse(trailingCommaRE.ReplaceAll(script, []byte("$1")))
	}
	if err != nil {
		return content, nil
	}
	job := detailPath(root, "props.pageProps.initialState.job")
	if job == nil {
		return content, nil
	}
	for target, spec := range fields {
		value := detailField(job, spec)
		if target == "locations" && value != nil {
			if s, ok := value.(string); ok {
				value = []string{s}
			}
		}
		content[target] = value
	}
	return content, nil
}
