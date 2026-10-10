package apisniffer

import (
	"encoding/json"
	"strings"
	"unicode"

	"github.com/jmespath/go-jmespath"
)

// Python JMESPath accepts legacy backtick string literals as well as JSON.
// Rewrite only that lexical fallback. Existing valid JSON and quoted strings
// keep their original spelling and expression semantics.
func pythonJMESExpression(expression string) (string, error) {
	var out strings.Builder
	var quote byte
	escaped := false
	for i := 0; i < len(expression); i++ {
		ch := expression[i]
		if quote != 0 {
			out.WriteByte(ch)
			if escaped {
				escaped = false
			} else if ch == '\\' {
				escaped = true
			} else if ch == quote {
				quote = 0
			}
			continue
		}
		if ch == '\'' || ch == '"' {
			quote = ch
			out.WriteByte(ch)
			continue
		}
		if ch != '`' {
			out.WriteByte(ch)
			continue
		}
		start := i + 1
		end := start
		escaped = false
		for end < len(expression) {
			c := expression[end]
			if escaped {
				escaped = false
			} else if c == '\\' {
				escaped = true
			} else if c == '`' {
				break
			}
			end++
		}
		if end == len(expression) {
			return "", ErrField
		}
		raw := strings.ReplaceAll(expression[start:end], "\\`", "`")
		out.WriteByte('`')
		if json.Valid([]byte(raw)) {
			out.WriteString(expression[start:end])
		} else {
			raw = strings.TrimLeftFunc(raw, func(r rune) bool { return unicode.IsSpace(r) || r >= 0x1c && r <= 0x1f })
			var value string
			if json.Unmarshal([]byte("\""+raw+"\""), &value) != nil {
				return "", ErrField
			}
			encoded, e := json.Marshal(value)
			if e != nil {
				return "", ErrField
			}
			out.WriteString(strings.ReplaceAll(string(encoded), "`", "\\`"))
		}
		out.WriteByte('`')
		i = end
	}
	if quote != 0 {
		return "", ErrField
	}
	return out.String(), nil
}
func compileJMESPath(expression string) (*jmespath.JMESPath, error) {
	normalized, e := pythonJMESExpression(expression)
	if e != nil {
		return nil, e
	}
	return jmespath.Compile(normalized)
}
func searchJMESPath(expression string, value any) (any, error) {
	compiled, e := compileJMESPath(expression)
	if e != nil {
		return nil, e
	}
	return compiled.Search(value)
}
