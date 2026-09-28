package jsonld

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"strconv"
	"strings"
	"unicode"
)

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

func emptyContent() map[string]any {
	result := make(map[string]any)
	for _, k := range []string{"title", "description", "locations", "employment_type", "job_location_type", "date_posted", "base_salary", "language", "extras", "metadata"} {
		result[k] = nil
	}
	return result
}

func pythonNumber(raw json.Number) string {
	if !strings.ContainsAny(raw.String(), ".eE") {
		return raw.String()
	}
	n, _ := raw.Float64()
	mode := byte('g')
	if math.Abs(n) >= 1e-4 && math.Abs(n) < 1e16 || n == 0 {
		mode = 'f'
	}
	s := strconv.FormatFloat(n, mode, -1, 64)
	if !strings.ContainsAny(s, ".e") {
		s += ".0"
	}
	return s
}

func decodeJSON(raw []byte) (any, error) {
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
func (o orderedObject) MarshalJSON() ([]byte, error) {
	m := make(map[string]any)
	for k, v := range o.values {
		m[k] = v
	}
	return json.Marshal(m)
}
func object(v any) orderedObject { o, _ := v.(orderedObject); return o }
func get(v any, k string) any    { return object(v).values[k] }
func text(v any) string          { s, _ := v.(string); return s }
func pySpace(r rune) bool        { return unicode.IsSpace(r) || (r >= 0x1c && r <= 0x1f) }
func trim(s string) string       { return strings.TrimFunc(s, pySpace) }
func words(s string) string      { return strings.Join(strings.FieldsFunc(s, pySpace), " ") }
func pyTruthy(v any) bool {
	if n, ok := v.(json.Number); ok {
		f, _ := n.Float64()
		return f != 0
	}
	return detailTruthy(v)
}
