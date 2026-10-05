package apisniffer

import (
	"encoding/json"
	"fmt"
	"reflect"
	"strings"
	"unicode/utf16"
)

// Stored POST objects use Python json.dumps' ASCII encoding and provider key
// order. Pagination uses its default spaces; initial configured bodies are
// compact. Preserve both rather than changing signed or byte-sensitive bodies.
func (d *Document) jsonBody(value any, spaced bool) (string, error) {
	comma, colon := ",", ":"
	if spaced {
		comma, colon = ", ", ": "
	}
	switch v := value.(type) {
	case map[string]any:
		keys, ok := d.order[reflect.ValueOf(v)]
		if !ok {
			return "", ErrOptions
		}
		parts := []string{}
		for _, k := range keys {
			key, _ := d.jsonBody(k, spaced)
			item, err := d.jsonBody(v[k], spaced)
			if err != nil {
				return "", err
			}
			parts = append(parts, key+colon+item)
		}
		return "{" + strings.Join(parts, comma) + "}", nil
	case []any:
		parts := []string{}
		for _, x := range v {
			item, err := d.jsonBody(x, spaced)
			if err != nil {
				return "", err
			}
			parts = append(parts, item)
		}
		return "[" + strings.Join(parts, comma) + "]", nil
	case string:
		var out strings.Builder
		out.WriteByte('"')
		for _, r := range v {
			if r >= 127 {
				for _, unit := range utf16.Encode([]rune{r}) {
					fmt.Fprintf(&out, `\u%04x`, unit)
				}
				continue
			}
			if r == '"' || r == '\\' {
				out.WriteByte('\\')
				out.WriteRune(r)
				continue
			}
			if r < 32 {
				switch r {
				case '\b':
					out.WriteString(`\b`)
				case '\f':
					out.WriteString(`\f`)
				case '\n':
					out.WriteString(`\n`)
				case '\r':
					out.WriteString(`\r`)
				case '\t':
					out.WriteString(`\t`)
				default:
					fmt.Fprintf(&out, `\u%04x`, r)
				}
				continue
			}
			out.WriteRune(r)
		}
		out.WriteByte('"')
		return out.String(), nil
	case json.Number:
		return pythonNumber(v), nil
	case nil:
		return "null", nil
	case bool, int:
		b, err := json.Marshal(v)
		return string(b), err
	}
	return "", ErrOptions
}
