package apisniffer

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"net/url"
	"regexp"
	"strings"
	"unicode"

	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
	"golang.org/x/text/cases"
	"golang.org/x/text/language"
)

var inlineBoundaryTag = regexp.MustCompile(`^[a-z][a-z0-9-]*$`)

func inlineTrim(s string) string {
	return strings.TrimFunc(s, func(r rune) bool { return unicode.IsSpace(r) || r >= 0x1c && r <= 0x1f })
}

func ValidateInlineBoundary(boundary dom.Object) error {
	if boundary == nil {
		return nil
	}
	if len(boundary) == 0 {
		return ErrOptions
	}
	configured := false
	for k, v := range boundary {
		if k != "tag" && k != "text" && k != "attr" && k != "match_regex" {
			return ErrOptions
		}
		s, ok := v.(string)
		if v == nil {
			continue
		}
		if !ok {
			return ErrOptions
		}
		configured = true
		switch k {
		case "tag":
			if len([]rune(s)) > 32 || !inlineBoundaryTag.MatchString(cases.Fold().String(s)) {
				return ErrOptions
			}
		case "text", "attr":
			if inlineTrim(s) == "" || len([]rune(s)) > 512 || strings.ContainsRune(s, 0) {
				return ErrOptions
			}
		case "match_regex":
			if s == "" || len([]rune(s)) > 2048 {
				return ErrOptions
			}
			if _, err := dom.CompileURLPattern("(?s)" + s); err != nil {
				return ErrOptions
			}
		default:
			return ErrOptions
		}
	}
	if !configured {
		return ErrOptions
	}
	return nil
}

func inlineBoundaryMatches(e dom.Element, b dom.Object) (bool, error) {
	if tag, ok := b["tag"].(string); ok && e.Tag != cases.Fold().String(tag) {
		return false, nil
	}
	if text, ok := b["text"].(string); ok && !strings.Contains(cases.Fold().String(e.Text), cases.Fold().String(text)) {
		return false, nil
	}
	if pattern, ok := b["match_regex"].(string); ok {
		re, err := dom.CompileURLPattern("(?s)" + pattern)
		if err != nil {
			return false, err
		}
		matched, err := re.MatchString(e.Text)
		if err != nil || !matched {
			return false, err
		}
	}
	if attr, ok := b["attr"].(string); ok {
		key, value, hasValue := strings.Cut(attr, "=")
		stored, present := e.Attrs[key]
		if !present || hasValue && !strings.Contains(stored, value) {
			return false, nil
		}
	}
	return true, nil
}

// ExtractInlineRows reuses the production DOM tokenizer/step engine while
// preserving the inline monitor's advancing cursor and bounded item sections.
// Row filtering, defaults, expiry and transport remain separate obligations.
func ExtractInlineRows(source string, steps []dom.Object, item, start, end dom.Object, includeHidden bool) ([]dom.Object, bool, error) {
	rows := []dom.Object{}
	for _, boundary := range []dom.Object{item, start, end} {
		if err := ValidateInlineBoundary(boundary); err != nil {
			return nil, false, err
		}
	}
	if (start == nil) != (end == nil) {
		return nil, false, ErrOptions
	}
	elements, err := dom.Flatten(source, includeHidden, false)
	if err != nil {
		return nil, false, err
	}
	if start != nil {
		starts, ends := []int{}, []int{}
		for i, e := range elements {
			matched, err := inlineBoundaryMatches(e, start)
			if err != nil {
				return nil, false, err
			}
			if matched {
				starts = append(starts, i)
			}
		}
		if len(starts) != 1 {
			return nil, false, ErrInventory
		}
		for i := starts[0] + 1; i < len(elements); i++ {
			matched, err := inlineBoundaryMatches(elements[i], end)
			if err != nil {
				return nil, false, err
			}
			if matched {
				ends = append(ends, i)
			}
		}
		if len(ends) != 1 {
			return nil, false, ErrInventory
		}
		elements = elements[starts[0]+1 : ends[0]]
	}
	cursor, processed := 0, 0
	for cursor < len(elements) && processed < 500 {
		var result dom.Object
		next := len(elements)
		if item == nil {
			result, next, err = dom.WalkSteps(elements, steps, cursor)
		} else {
			lo := len(elements)
			for i := cursor; i < len(elements); i++ {
				matched, e := inlineBoundaryMatches(elements[i], item)
				if e != nil {
					return nil, false, e
				}
				if matched {
					lo = i
					break
				}
			}
			if lo == len(elements) {
				break
			}
			for i := lo + 1; i < len(elements); i++ {
				matched, e := inlineBoundaryMatches(elements[i], item)
				if e != nil {
					return nil, false, e
				}
				if matched {
					next = i
					break
				}
			}
			result, _, err = dom.WalkSteps(elements[lo:next], steps, 0)
		}
		if err != nil {
			return nil, false, err
		}
		if next <= cursor {
			break
		}
		if !detailTruthy(result["title"]) {
			if item == nil {
				break
			}
			cursor = next
			continue
		}
		cursor = next
		processed++
		rows = append(rows, result)
	}
	return rows, processed >= 500 && cursor < len(elements), nil
}

// Synthetic URLs retain query key/value order and title-collision numbering.
// Stable provider fields use case folding and fail on any repeated identity.
func validInlineIdentityBase(raw string) bool {
	u, err := url.Parse(raw)
	return err == nil && len(raw) <= 8192 && (u.Scheme == "http" || u.Scheme == "https") && u.Hostname() != "" && u.User == nil && u.Opaque == "" && !strings.ContainsAny(raw, "\r\n\x00")
}

func InlineSyntheticURL(boardURL, title string, seen map[string]int, stable *string) (string, error) {
	if !validInlineIdentityBase(boardURL) || seen == nil {
		return "", ErrOptions
	}
	identity, normalized := title, cases.Lower(language.Und).String(inlineTrim(title))
	if stable != nil {
		identity = *stable
		normalized = cases.Fold().String(inlineTrim(identity))
	}
	slug := nextdataSlug(identity)
	if len(slug) > 50 {
		slug = slug[:50]
	}
	hash := sha256.Sum256([]byte(normalized))
	jid := hex.EncodeToString(hash[:])[:6]
	if slug != "" {
		jid = slug + "-" + jid
	}
	count := seen[jid]
	seen[jid] = count + 1
	if stable != nil && count > 0 {
		return "", ErrInventory
	}
	if count > 0 {
		jid = fmt.Sprintf("%s-%d", jid, count+1)
	}
	return InlineIdentityURL(boardURL, jid)
}

func InlineIdentityURL(boardURL, identity string) (string, error) {
	if !validInlineIdentityBase(boardURL) {
		return "", ErrOptions
	}
	base, fragment, _ := strings.Cut(boardURL, "#")
	prefix, query, _ := strings.Cut(base, "?")
	query = strings.ReplaceAll(nextdataQuery(query, "_jid", identity), "%7E", "~")
	result := prefix + "?" + query
	if fragment != "" {
		result += "#" + fragment
	}
	return result, nil
}
