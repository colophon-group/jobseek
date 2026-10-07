package worker

import (
	"bytes"
	"encoding/json"
	"errors"
	"math"
	"reflect"
	"strconv"
	"strings"
)

func feedPythonFloat(value float64) string {
	if math.IsNaN(value) {
		return "NaN"
	}
	if math.IsInf(value, 1) {
		return "Infinity"
	}
	if math.IsInf(value, -1) {
		return "-Infinity"
	}
	format := byte('f')
	abs := math.Abs(value)
	if abs != 0 && (abs < 1e-4 || abs >= 1e16) {
		format = 'e'
	}
	s := strconv.FormatFloat(value, format, -1, 64)
	if !strings.ContainsAny(s, ".e") {
		s += ".0"
	}
	return s
}

// Preserve Python's numeric kind and float representation before JSON encoding.
func feedJSONValue(value any, depth int) (any, error) {
	if depth > 128 {
		return nil, errors.New("job-filter metadata nesting exceeded")
	}
	if number, ok := value.(json.Number); ok {
		if !strings.ContainsAny(number.String(), ".eE") {
			return number, nil
		}
		v, err := number.Float64()
		if err != nil {
			return nil, err
		}
		return json.Number(feedPythonFloat(v)), nil
	}
	v := reflect.ValueOf(value)
	if !v.IsValid() {
		return nil, nil
	}
	for v.Kind() == reflect.Pointer || v.Kind() == reflect.Interface {
		if v.IsNil() {
			return nil, nil
		}
		v = v.Elem()
	}
	switch v.Kind() {
	case reflect.Float32, reflect.Float64:
		return json.Number(feedPythonFloat(v.Float())), nil
	case reflect.Map:
		if v.IsNil() {
			return nil, nil
		}
		if v.Type().Key().Kind() != reflect.String {
			return nil, errors.New("job-filter metadata key is not text")
		}
		out := map[string]any{}
		iter := v.MapRange()
		for iter.Next() {
			converted, err := feedJSONValue(iter.Value().Interface(), depth+1)
			if err != nil {
				return nil, err
			}
			out[iter.Key().String()] = converted
		}
		return out, nil
	case reflect.Slice, reflect.Array:
		if v.Kind() == reflect.Slice && v.IsNil() {
			return nil, nil
		}
		out := make([]any, v.Len())
		for n := range out {
			var err error
			out[n], err = feedJSONValue(v.Index(n).Interface(), depth+1)
			if err != nil {
				return nil, err
			}
		}
		return out, nil
	default:
		return v.Interface(), nil
	}
}

// Match json.dumps(...,ensure_ascii=False,sort_keys=True), including default
// separators and literal Unicode line separators inside quoted values.
func feedJSONText(value any) (string, error) {
	normalized, err := feedJSONValue(value, 0)
	if err != nil {
		return "", err
	}
	var buffer bytes.Buffer
	encoder := json.NewEncoder(&buffer)
	encoder.SetEscapeHTML(false)
	if err := encoder.Encode(normalized); err != nil {
		return "", err
	}
	source := strings.TrimSuffix(buffer.String(), "\n")
	var out strings.Builder
	quoted := false
	for n := 0; n < len(source); n++ {
		c := source[n]
		if quoted && c == '\\' && n+1 < len(source) {
			if strings.HasPrefix(source[n:], `\u2028`) {
				out.WriteRune('\u2028')
				n += 5
				continue
			}
			if strings.HasPrefix(source[n:], `\u2029`) {
				out.WriteRune('\u2029')
				n += 5
				continue
			}
			out.WriteByte(c)
			n++
			out.WriteByte(source[n])
			continue
		}
		if c == '"' {
			quoted = !quoted
		}
		out.WriteByte(c)
		if !quoted && (c == ',' || c == ':') {
			out.WriteByte(' ')
		}
	}
	return out.String(), nil
}

func feedJobFilterText(job RichMonitorJob, field string) (string, error) {
	text := func(value *string) string {
		if value == nil {
			return ""
		}
		return *value
	}
	metadata := job.Metadata
	if metadata == nil {
		metadata = map[string]any{}
	}
	switch field {
	case "title":
		return text(job.Title), nil
	case "description":
		return text(job.Description), nil
	case "locations":
		return strings.Join(job.Locations, "\n"), nil
	case "metadata":
		return feedJSONText(metadata)
	}
	if strings.HasPrefix(field, "metadata.") {
		value := metadata[strings.TrimPrefix(field, "metadata.")]
		if value == nil {
			return "", nil
		}
		if value, ok := value.(string); ok {
			return value, nil
		}
		return feedJSONText(value)
	}
	parts := []string{}
	for _, value := range []string{text(job.Title), text(job.Description), strings.Join(job.Locations, "\n")} {
		if value != "" {
			parts = append(parts, value)
		}
	}
	if len(metadata) > 0 {
		encoded, err := feedJSONText(metadata)
		if err != nil {
			return "", err
		}
		parts = append(parts, encoded)
	}
	return strings.Join(parts, "\n"), nil
}
