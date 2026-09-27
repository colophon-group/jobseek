package enrichment

import (
	"regexp"
	"strings"
	"unicode"

	"golang.org/x/text/cases"
	"golang.org/x/text/language"
	"golang.org/x/text/unicode/norm"
)

func space(r rune) bool     { return unicode.IsSpace(r) || r >= 0x1c && r <= 0x1f }
func word(r rune) bool      { return r == '_' || unicode.IsLetter(r) || unicode.IsNumber(r) }
func lower(s string) string { return cases.Lower(language.Und).String(s) }

var genderParen = regexp.MustCompile(`\((?:ne|e|euse)\)`)
var genderMarker = regexp.MustCompile(`\([hfmwdx/]+\)`)
var genderSlash = regexp.MustCompile(`/(?:ne|euse|e)`)
var asciiTokens = regexp.MustCompile(`[a-z0-9]+`)

func Normalize(s string) string {
	s = norm.NFKD.String(s)
	var b strings.Builder
	for _, r := range s {
		// Python unicodedata.combining removes only nonzero combining classes;
		// spacing marks with class zero must remain.
		if norm.NFD.PropertiesString(string(r)).CCC() == 0 {
			b.WriteRune(r)
		}
	}
	s = strings.TrimFunc(lower(b.String()), space)
	s = genderParen.ReplaceAllString(s, "")
	matches := genderSlash.FindAllStringIndex(s, -1)
	var out strings.Builder
	from := 0
	for _, p := range matches {
		rest := []rune(s[p[1]:])
		if len(rest) != 0 && word(rest[0]) {
			continue
		}
		out.WriteString(s[from:p[0]])
		from = p[1]
	}
	out.WriteString(s[from:])
	return strings.Join(strings.FieldsFunc(genderMarker.ReplaceAllString(out.String(), ""), space), " ")
}
func tokenSet(s string) map[string]bool {
	out := map[string]bool{}
	for _, s := range asciiTokens.FindAllString(s, -1) {
		out[s] = true
	}
	return out
}
