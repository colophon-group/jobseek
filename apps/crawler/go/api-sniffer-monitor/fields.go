package apisniffer

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"html"
	"io"
	"math"
	"reflect"
	"sort"
	"strconv"
	"strings"
	"time"
	"unicode"

	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
	"github.com/jmespath/go-jmespath"
)

var ErrField = errors.New("invalid configured JSON field extraction")

// Document retains JSON number spelling and object insertion order for Python's
// scalar/list coercion. JMESPath still selects the provider's original values.
type Document struct {
	Value any
	Root  any
	order map[reflect.Value][]string
}

// ObjectKeys returns the provider's original key order for a decoded object.
// A detached copy prevents consumers from changing retained JSON provenance.
func (d *Document) ObjectKeys(object map[string]any) []string {
	if d == nil || object == nil {
		return nil
	}
	return append([]string(nil), d.order[reflect.ValueOf(object)]...)
}

func Decode(body []byte) (*Document, error) {
	d := &Document{order: map[reflect.Value][]string{}}
	p := json.NewDecoder(bytes.NewReader(body))
	p.UseNumber()
	v, err := d.decode(p)
	if err != nil || p.Decode(new(any)) != io.EOF {
		return nil, ErrField
	}
	d.Value = v
	return d, nil
}

func (d *Document) decode(p *json.Decoder) (any, error) {
	t, err := p.Token()
	if err != nil {
		return nil, err
	}
	switch t {
	case json.Delim('{'):
		obj := map[string]any{}
		keys := []string{}
		for p.More() {
			key, err := p.Token()
			if err != nil {
				return nil, err
			}
			k, ok := key.(string)
			if !ok {
				return nil, ErrField
			}
			v, err := d.decode(p)
			if err != nil {
				return nil, err
			}
			if _, exists := obj[k]; !exists {
				keys = append(keys, k)
			}
			obj[k] = v
		}
		if end, err := p.Token(); err != nil || end != json.Delim('}') {
			return nil, ErrField
		}
		d.order[reflect.ValueOf(obj)] = keys
		return obj, nil
	case json.Delim('['):
		arr := []any{}
		for p.More() {
			v, err := d.decode(p)
			if err != nil {
				return nil, err
			}
			arr = append(arr, v)
		}
		if end, err := p.Token(); err != nil || end != json.Delim(']') {
			return nil, ErrField
		}
		return arr, nil
	default:
		return t, nil
	}
}

func floatJSON(value any) any {
	switch v := value.(type) {
	case json.Number:
		n, _ := v.Float64()
		return n
	case []any:
		out := make([]any, len(v))
		for i, x := range v {
			out[i] = floatJSON(x)
		}
		return out
	case map[string]any:
		out := map[string]any{}
		for k, x := range v {
			out[k] = floatJSON(x)
		}
		return out
	}
	return value
}

func Search(value any, path string) (any, error) {
	if path == "" || path == "$" {
		return value, nil
	}
	// Plain paths/projections preserve integer-vs-float coercion. Numeric
	// predicates use the JMESPath implementation's standard JSON number type.
	if !strings.Contains(path, "[?") {
		if found, err := jmespath.Search(path, value); err == nil {
			return found, nil
		}
	}
	return jmespath.Search(path, floatJSON(value))
}

func quote(value string) string {
	q := '\''
	if strings.Contains(value, "'") && !strings.Contains(value, `"`) {
		q = '"'
	}
	var b strings.Builder
	b.WriteRune(q)
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
			if r == q {
				b.WriteRune('\\')
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
	b.WriteRune(q)
	return b.String()
}

func pythonNumber(n json.Number) string {
	if !strings.ContainsAny(string(n), ".eE") {
		if n == "-0" {
			return "0"
		}
		return string(n)
	}
	v, err := n.Float64()
	if err != nil {
		return string(n)
	}
	mode := byte('g')
	if math.Abs(v) >= 1e-4 && math.Abs(v) < 1e16 || v == 0 {
		mode = 'f'
	}
	s := strconv.FormatFloat(v, mode, -1, 64)
	if !strings.ContainsAny(s, ".eE") {
		s += ".0"
	}
	return s
}

func (d *Document) String(value any) (string, error) { return d.stringify(value, false) }
func (d *Document) stringify(value any, repr bool) (string, error) {
	switch v := value.(type) {
	case nil:
		return "None", nil
	case string:
		if repr {
			return quote(v), nil
		}
		return v, nil
	case bool:
		if v {
			return "True", nil
		}
		return "False", nil
	case json.Number:
		return pythonNumber(v), nil
	case float64:
		return strconv.FormatFloat(v, 'g', -1, 64), nil
	case []any:
		parts := make([]string, len(v))
		for i, x := range v {
			s, err := d.stringify(x, true)
			if err != nil {
				return "", err
			}
			parts[i] = s
		}
		return "[" + strings.Join(parts, ", ") + "]", nil
	case map[string]any:
		keys, ok := d.order[reflect.ValueOf(v)]
		if !ok {
			return "", ErrField
		} // No invented ordering for computed JSON objects.
		parts := []string{}
		for _, k := range keys {
			s, err := d.stringify(v[k], true)
			if err != nil {
				return "", err
			}
			parts = append(parts, quote(k)+": "+s)
		}
		return "{" + strings.Join(parts, ", ") + "}", nil
	}
	return "", ErrField
}

func (d *Document) texts(v any) (any, error) {
	if v == nil {
		return nil, nil
	}
	if a, ok := v.([]any); ok {
		out := []any{}
		for _, x := range a {
			if x != nil {
				s, err := d.String(x)
				if err != nil {
					return nil, err
				}
				out = append(out, s)
			}
		}
		if len(out) == 0 {
			return nil, nil
		}
		return out, nil
	}
	return d.String(v)
}

// Field matches shared.nextdata.extract_field, including heading ownership in
// concat specs, root lookups, boolean maps, object-key selection and timestamps.
func (d *Document) Field(item any, spec any) (any, error) {
	if a, ok := spec.([]any); ok {
		return d.concat(item, a, "\n")
	}
	if o, ok := spec.(map[string]any); ok {
		if a, exists := o["concat"]; exists {
			list, ok := a.([]any)
			if !ok {
				return nil, ErrField
			}
			sep := "\n"
			if s, exists := o["separator"]; exists {
				var valid bool
				sep, valid = s.(string)
				if !valid {
					return nil, ErrField
				}
			}
			return d.concat(item, list, sep)
		}
		if path, ok := o["lookup_from"].(string); ok {
			keyPath, ok := o["key_from"].(string)
			if !ok {
				return nil, ErrField
			}
			key, err := Search(item, keyPath)
			if err != nil || key == nil {
				return nil, err
			}
			root := d.Root
			if root == nil {
				root = d.Value
			}
			table, err := Search(root, path)
			if err != nil {
				return nil, err
			}
			obj, ok := table.(map[string]any)
			if !ok {
				return nil, nil
			}
			k, err := d.String(key)
			if err != nil {
				return nil, err
			}
			return obj[k], nil
		}
		path, ok := o["path"].(string)
		if !ok {
			return nil, ErrField
		}
		var value any
		var err error
		if pattern, exists := o["key_pattern"]; exists {
			p, ok := pattern.(string)
			if !ok || p == "" || len(p) > 256 || len(o) != 2 {
				return nil, ErrField
			}
			rx, err := dom.CompileURLPattern(p)
			if err != nil {
				return nil, ErrField
			}
			v, err := Search(item, path)
			if err != nil || v == nil {
				return nil, err
			}
			obj, ok := v.(map[string]any)
			if !ok {
				return nil, ErrField
			}
			keys := []string{}
			for k := range obj {
				keys = append(keys, k)
			}
			sort.Strings(keys)
			values := []any{}
			for _, k := range keys {
				match, err := rx.MatchString(k)
				if err != nil {
					return nil, ErrField
				}
				if !match {
					continue
				}
				v := obj[k]
				if a, ok := v.([]any); ok {
					for _, x := range a {
						if x != nil {
							s, err := d.String(x)
							if err != nil {
								return nil, err
							}
							values = append(values, s)
						}
					}
				} else if v != nil {
					s, err := d.String(v)
					if err != nil {
						return nil, err
					}
					values = append(values, s)
				}
			}
			if len(values) == 0 {
				return nil, nil
			}
			if len(values) == 1 {
				return values[0], nil
			}
			return values, nil
		}
		if rawMap, exists := o["map"]; exists {
			mapping, ok := rawMap.(map[string]any)
			if !ok {
				return nil, ErrField
			}
			v, failure := Search(item, path)
			if failure != nil {
				return nil, failure
			}
			mapOne := func(x any) (any, error) {
				key, err := d.String(x)
				if err != nil {
					return nil, err
				}
				mapped, exists := mapping[key]
				if !exists {
					return nil, nil
				}
				return d.String(mapped)
			}
			if a, ok := v.([]any); ok {
				out := []any{}
				for _, x := range a {
					mapped, err := mapOne(x)
					if err != nil {
						return nil, err
					}
					if mapped != nil {
						out = append(out, mapped)
					}
				}
				if len(out) > 0 {
					value = out
				}
			} else if v != nil {
				key, failure := d.String(v)
				if failure != nil {
					return nil, failure
				}
				if mapped := mapping[key]; mapped != nil {
					value, err = d.String(mapped)
				}
			}
		} else {
			value, err = d.Field(item, path)
		}
		if err != nil || value == nil {
			return nil, err
		}
		if unescape, _ := o["html_unescape"].(bool); unescape {
			return transform(value, func(s string) (string, error) { return html.UnescapeString(s), nil })
		}
		if unit, exists := o["timestamp_unit"]; exists {
			u, ok := unit.(string)
			if !ok || u != "seconds" && u != "milliseconds" {
				return nil, ErrField
			}
			return transform(value, func(s string) (string, error) {
				n, err := strconv.ParseFloat(s, 64)
				if u == "milliseconds" {
					n /= 1000
				}
				if err != nil || math.IsNaN(n) || math.IsInf(n, 0) || n < -62135596800 || n >= 253402300800 {
					return "", ErrField
				}
				sec, frac := math.Modf(n)
				t := time.Unix(int64(sec), int64(math.Round(frac*1e6))*1000).UTC()
				out := t.Format("2006-01-02T15:04:05")
				if t.Nanosecond() != 0 {
					out += fmt.Sprintf(".%06d", t.Nanosecond()/1000)
				}
				return out + "+00:00", nil
			})
		}
		return value, nil
	}
	path, ok := spec.(string)
	if !ok {
		return nil, ErrField
	}
	if strings.HasPrefix(path, "=") {
		return path[1:], nil
	}
	v, err := Search(item, path)
	if err != nil {
		return nil, ErrField
	}
	return d.texts(v)
}

func transform(value any, f func(string) (string, error)) (any, error) {
	if s, ok := value.(string); ok {
		return f(s)
	}
	if a, ok := value.([]any); ok {
		out := make([]any, len(a))
		for i, x := range a {
			s, ok := x.(string)
			if !ok {
				return nil, ErrField
			}
			v, err := f(s)
			if err != nil {
				return nil, err
			}
			out[i] = v
		}
		return out, nil
	}
	return nil, ErrField
}

func plainHTML(s string) string {
	for i := 0; i+1 < len(s); i++ {
		if s[i] == '<' && (s[i+1] == '/' || s[i+1] >= 'a' && s[i+1] <= 'z' || s[i+1] >= 'A' && s[i+1] <= 'Z') {
			return s
		}
	}
	if !strings.Contains(s, "\n") {
		return s
	}
	out := []string{}
	list := false
	for _, line := range strings.Split(s, "\n") {
		line = strings.TrimSpace(line)
		if strings.HasPrefix(line, "- ") {
			if !list {
				out = append(out, "<ul>")
				list = true
			}
			out = append(out, "<li>"+line[2:]+"</li>")
		} else {
			if list {
				out = append(out, "</ul>")
				list = false
			}
			out = append(out, line+"<br>")
		}
	}
	if list {
		out = append(out, "</ul>")
	}
	for len(out) > 0 && out[len(out)-1] == "<br>" {
		out = out[:len(out)-1]
	}
	if len(out) > 0 {
		out[len(out)-1] = strings.TrimSuffix(out[len(out)-1], "<br>")
	}
	return strings.Join(out, "\n")
}

func (d *Document) concat(item any, specs []any, sep string) (any, error) {
	parts := []string{}
	pending := []string{}
	data := false
	for _, spec := range specs {
		if s, ok := spec.(string); ok && strings.HasPrefix(s, "=") {
			pending = append(pending, s[1:])
			continue
		}
		data = true
		if o, ok := spec.(map[string]any); ok {
			path, _ := o["each"].(string)
			wrap, _ := o["wrap"].(string)
			v, err := Search(item, path)
			if err != nil {
				return nil, err
			}
			arr, ok := v.([]any)
			if !ok || len(arr) == 0 {
				pending = nil
				continue
			}
			parts = append(parts, pending...)
			pending = nil
			for _, x := range arr {
				obj, ok := x.(map[string]any)
				if !ok {
					s, err := d.String(x)
					if err != nil {
						return nil, err
					}
					parts = append(parts, s)
					continue
				}
				rendered := wrap
				keys := d.order[reflect.ValueOf(obj)]
				if keys == nil {
					return nil, ErrField
				}
				for _, k := range keys {
					s := ""
					if obj[k] != nil {
						var err error
						s, err = d.String(obj[k])
						if err != nil {
							return nil, err
						}
					}
					rendered = strings.ReplaceAll(rendered, "{"+k+"}", s)
				}
				parts = append(parts, rendered)
			}
			continue
		}
		path, ok := spec.(string)
		if !ok {
			return nil, ErrField
		}
		v, err := Search(item, path)
		if err != nil {
			return nil, err
		}
		if v == nil {
			pending = nil
			continue
		}
		texts, err := d.texts(v)
		if err != nil {
			return nil, err
		}
		valid := []string{}
		if a, ok := texts.([]any); ok {
			for _, x := range a {
				s := x.(string)
				if strings.TrimSpace(s) != "" {
					valid = append(valid, plainHTML(s))
				}
			}
		} else if s, ok := texts.(string); ok && strings.TrimSpace(s) != "" {
			valid = append(valid, plainHTML(s))
		}
		if len(valid) == 0 {
			pending = nil
			continue
		}
		parts = append(parts, pending...)
		pending = nil
		parts = append(parts, valid...)
	}
	if len(parts) > 0 || !data {
		parts = append(parts, pending...)
	}
	if len(parts) == 0 {
		return nil, nil
	}
	return strings.Join(parts, sep), nil
}
