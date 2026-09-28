package dom

import (
	"errors"
	"strings"
	"time"

	"github.com/dlclark/regexp2/v2"
)

const wordClass = `[\p{L}\p{N}_]`
const spaceClass = `[\p{Z}\t\n\r\f\v\u0085\u001c-\u001f]`

func pythonEscape(c byte) string {
	switch c {
	case 'w':
		return wordClass
	case 'W':
		return `[^\p{L}\p{N}_]`
	case 's':
		return spaceClass
	case 'S':
		return `[^\p{Z}\t\n\r\f\v\u0085\u001c-\u001f]`
	case 'd':
		return `[\p{Nd}]`
	case 'D':
		return `[^\p{Nd}]`
	case 'b':
		return `(?:(?<!` + wordClass + `)(?=` + wordClass + `)|(?<=` + wordClass + `)(?!` + wordClass + `))`
	case 'B':
		return `(?:(?<!` + wordClass + `)(?!` + wordClass + `)|(?<=` + wordClass + `)(?=` + wordClass + `))`
	case 'Z':
		return `\z`
	default:
		return `\` + string(c)
	}
}

// Preserve Python's Unicode word/space classes (including non-decimal numbers
// and U+001c..001f), rather than the .NET engine's combining-mark word class.
func pythonPattern(pattern string) (string, error) {
	var out strings.Builder
	for i := 0; i < len(pattern); i++ {
		c := pattern[i]
		if c == '\\' {
			i++
			if i == len(pattern) {
				return "", errors.New("trailing regex escape")
			}
			out.WriteString(pythonEscape(pattern[i]))
			continue
		}
		if c != '[' {
			if c == '(' && strings.HasPrefix(pattern[i:], "(?P=") {
				if end := strings.IndexByte(pattern[i+4:], ')'); end >= 0 {
					out.WriteString(`\k<` + pattern[i+4:i+4+end] + `>`)
					i += 4 + end
					continue
				}
			}
			out.WriteByte(c)
			continue
		}
		start := i
		i++
		negated := i < len(pattern) && pattern[i] == '^'
		if negated {
			i++
		}
		var ordinary strings.Builder
		branches := []string{}
		if i < len(pattern) && pattern[i] == ']' {
			ordinary.WriteString(`\]`)
			i++
		}
		closed := false
		for ; i < len(pattern); i++ {
			if pattern[i] == ']' {
				closed = true
				break
			}
			if pattern[i] == '\\' && i+1 < len(pattern) {
				i++
				c = pattern[i]
				if strings.ContainsRune("wWsSdD", rune(c)) {
					branches = append(branches, pythonEscape(c))
				} else if c == 'b' {
					ordinary.WriteString(`\x08`)
				} else {
					ordinary.WriteByte('\\')
					ordinary.WriteByte(c)
				}
			} else {
				ordinary.WriteByte(pattern[i])
			}
		}
		if !closed {
			return "", errors.New("unclosed regex character class")
		}
		if len(branches) == 0 {
			out.WriteString(pattern[start : i+1])
			continue
		}
		if ordinary.Len() > 0 {
			branches = append(branches, "["+ordinary.String()+"]")
		}
		union := "(?:" + strings.Join(branches, "|") + ")"
		if negated {
			out.WriteString("(?:(?!" + union + ")[\\s\\S])")
		} else {
			out.WriteString(union)
		}
	}
	translated := strings.ReplaceAll(out.String(), "(?P<", "(?<")
	// Python's default Unicode flag does not change the translated classes.
	translated = strings.ReplaceAll(translated, "(?u)", "")
	return translated, nil
}

type regexCache map[string]*regexp2.Regexp

func (cache regexCache) compile(pattern string, dotall bool) (*regexp2.Regexp, error) {
	key := pattern
	if dotall {
		key = "s\x00" + pattern
	}
	if re := cache[key]; re != nil {
		return re, nil
	}
	translated, err := pythonPattern(pattern)
	if err != nil {
		return nil, err
	}
	options := regexp2.None
	if dotall {
		options = regexp2.Singleline
	}
	re, err := regexp2.Compile(translated, options, regexp2.OptionMaintainCaptureOrder(), regexp2.OptionMaxBacktrackingStackSize(1<<20))
	if err != nil {
		return nil, err
	}
	re.MatchTimeout = 2 * time.Second
	cache[key] = re
	return re, nil
}
func (cache regexCache) matches(pattern, text string, dotall bool) (bool, error) {
	re, err := cache.compile(pattern, dotall)
	if err != nil {
		return false, err
	}
	return re.MatchString(text)
}
func (cache regexCache) capture(pattern, text string) (string, error) {
	re, err := cache.compile(pattern, true)
	if err != nil {
		return "", err
	}
	match, err := re.FindStringMatch(text)
	if err != nil {
		return "", err
	}
	if match == nil {
		return text, nil
	}
	group := match.GroupByNumber(1)
	if group == nil {
		return "", errors.New("regex capture group 1 is absent")
	}
	if len(group.Captures) == 0 {
		return "", errors.New("regex capture group 1 did not participate")
	}
	return trim(group.String()), nil
}
