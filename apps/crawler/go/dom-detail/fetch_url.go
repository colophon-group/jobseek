package dom

import (
	"errors"
	"net/url"
	"strconv"
	"strings"
	"unicode/utf8"

	"github.com/dlclark/regexp2/v2"
)

var ErrFetchURL = errors.New("invalid alternate DOM read URL")

// AlternateFetchURL preserves the canonical caller identity while validating
// one bounded Python-compatible rewrite for the separately verified transport.
func AlternateFetchURL(source string, value any) (string, error) {
	if value == nil {
		return source, nil
	}
	re, replacement, e := alternateFetchTransform(value)
	if e != nil {
		return "", e
	}
	m, e := re.FindStringMatch(source)
	if e != nil || m == nil {
		return "", ErrFetchURL
	}
	next, e := re.FindNextMatch(m)
	if e != nil || next != nil {
		return "", ErrFetchURL
	}
	expanded, e := pythonReplacement(m, replacement)
	if e != nil {
		return "", e
	}
	target, e := re.ReplaceFunc(source, func(regexp2.Match) string { return expanded }, 0, 1)
	if e != nil || utf8.RuneCountInString(target) > 8192 || strings.ContainsRune(target, 0) {
		return "", ErrFetchURL
	}
	parsed, e := url.Parse(target)
	if e != nil || parsed.Scheme != "http" && parsed.Scheme != "https" || parsed.Hostname() == "" || parsed.User != nil {
		return "", ErrFetchURL
	}
	return target, nil
}

func pythonReplacement(m *regexp2.Match, s string) (string, error) {
	var out strings.Builder
	group := func(name string) error {
		n, e := strconv.Atoi(name)
		var g *regexp2.Group
		if e == nil {
			g = m.GroupByNumber(n)
		} else {
			g = m.GroupByName(name)
		}
		if g == nil {
			return ErrFetchURL
		}
		out.WriteString(g.String())
		return nil
	}
	for i := 0; i < len(s); i++ {
		if s[i] != '\\' {
			out.WriteByte(s[i])
			continue
		}
		i++
		if i >= len(s) {
			return "", ErrFetchURL
		}
		c := s[i]
		if c == 'g' {
			if i+1 >= len(s) || s[i+1] != '<' {
				return "", ErrFetchURL
			}
			end := strings.IndexByte(s[i+2:], '>')
			if end < 0 {
				return "", ErrFetchURL
			}
			name := s[i+2 : i+2+end]
			if name == "" || group(name) != nil {
				return "", ErrFetchURL
			}
			i += 2 + end
			continue
		}
		if c >= '1' && c <= '9' {
			end := i + 1
			for end < len(s) && end < i+2 && s[end] >= '0' && s[end] <= '9' {
				end++
			}
			if end < len(s) && end == i+2 && s[end] >= '0' && s[end] <= '7' && c <= '7' && s[i+1] <= '7' {
				n, _ := strconv.ParseInt(s[i:end+1], 8, 32)
				if n > 255 {
					return "", ErrFetchURL
				}
				out.WriteRune(rune(n))
				i = end
				continue
			}
			if group(s[i:end]) != nil {
				return "", ErrFetchURL
			}
			i = end - 1
			continue
		}
		if c == '0' {
			end := i + 1
			for end < len(s) && end < i+3 && s[end] >= '0' && s[end] <= '7' {
				end++
			}
			n, _ := strconv.ParseInt(s[i:end], 8, 32)
			out.WriteRune(rune(n))
			i = end - 1
			continue
		}
		escapes := map[byte]byte{'n': '\n', 'r': '\r', 't': '\t', 'f': '\f', 'v': '\v', 'a': '\a', 'b': '\b', '\\': '\\'}
		if v, ok := escapes[c]; ok {
			out.WriteByte(v)
		} else if c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z' {
			return "", ErrFetchURL
		} else {
			out.WriteByte('\\')
			out.WriteByte(c)
		}
	}
	return out.String(), nil
}

// ValidateFetchURLTransform checks a bound template before any posting exists.
// A concrete source must still pass AlternateFetchURL before each origin read.
func ValidateFetchURLTransform(value any) error {
	if value == nil {
		return nil
	}
	_, _, err := alternateFetchTransform(value)
	return err
}

func alternateFetchTransform(value any) (*regexp2.Regexp, string, error) {
	c, ok := value.(map[string]any)
	if !ok || len(c) != 2 {
		return nil, "", ErrFetchURL
	}
	find, fok := c["find"].(string)
	replacement, rok := c["replace"].(string)
	if !fok || !rok || find == "" || replacement == "" || utf8.RuneCountInString(find) > 2048 || utf8.RuneCountInString(replacement) > 4096 || strings.ContainsRune(find, 0) || strings.ContainsRune(replacement, 0) {
		return nil, "", ErrFetchURL
	}
	re, e := CompileURLPattern(find)
	if e != nil {
		return nil, "", ErrFetchURL
	}
	return re, replacement, nil
}
