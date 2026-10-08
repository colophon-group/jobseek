package apisniffer

import (
	"encoding/json"
	"math/big"
	"strings"

	"github.com/jmespath/go-jmespath"
)

func emptyResponseOptions(value any) (map[string]any, error) {
	m, ok := value.(map[string]any)
	if !ok || len(m) == 0 || len(m) > 32 {
		return nil, ErrOptions
	}
	for path, expected := range m {
		if path == "" || len([]rune(path)) > 256 || strings.ContainsRune(path, 0) {
			return nil, ErrOptions
		}
		if _, err := jmespath.Compile(path); err != nil {
			return nil, ErrOptions
		}
		switch value := expected.(type) {
		case nil, bool, string, json.Number:
		case []any:
			if len(value) != 0 {
				return nil, ErrOptions
			}
		default:
			return nil, ErrOptions
		}
	}
	return m, nil
}

// Match Python's exact scalar type check: integer 0, float 0.0 and false are
// distinct markers. Missing paths retain resolve_path's None behavior.
func emptyScalarEqual(actual, expected any) bool {
	switch expected := expected.(type) {
	case nil:
		return actual == nil
	case bool:
		value, ok := actual.(bool)
		return ok && value == expected
	case string:
		value, ok := actual.(string)
		return ok && value == expected
	case []any:
		value, ok := actual.([]any)
		return ok && len(value) == 0 && len(expected) == 0
	case json.Number:
		value, ok := actual.(json.Number)
		if !ok {
			return false
		}
		isFloat := func(value json.Number) bool { return strings.ContainsAny(string(value), ".eE") }
		if isFloat(value) != isFloat(expected) {
			return false
		}
		if isFloat(value) {
			a, e1 := value.Float64()
			b, e2 := expected.Float64()
			return e1 == nil && e2 == nil && a == b
		}
		a, ok1 := new(big.Int).SetString(string(value), 10)
		b, ok2 := new(big.Int).SetString(string(expected), 10)
		return ok1 && ok2 && a.Cmp(b) == 0
	}
	return false
}

func (d *Document) MatchesEmptyResponse(config map[string]any) (bool, error) {
	validated, err := emptyResponseOptions(config)
	if err != nil || d == nil {
		return false, ErrOptions
	}
	if _, ok := d.Value.(map[string]any); !ok {
		return false, nil
	}
	for path, expected := range validated {
		actual, err := Search(d.Value, path)
		if err != nil {
			return false, err
		}
		if !emptyScalarEqual(actual, expected) {
			return false, nil
		}
	}
	return true, nil
}
