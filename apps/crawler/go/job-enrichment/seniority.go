package enrichment

import (
	"regexp"
	"strings"
	"unicode"
	"unicode/utf8"
)

type prefixRule struct {
	pattern *regexp.Regexp
	slug    string
}

var prefixes = []prefixRule{
	{regexp.MustCompile(`^(?:senior|sr\.?) `), "senior"},
	{regexp.MustCompile(`^(?:junior|jr\.?) `), "entry"},
	{regexp.MustCompile(`^(?:principal|distinguished) `), "principal"},
	{regexp.MustCompile(`^staff `), "staff"},
}

func ptr(s string) *string { return &s }
func boundaryAfter(s string, end int) bool {
	if end == len(s) {
		return true
	}
	r, _ := utf8.DecodeRuneInString(s[end:])
	return !word(r)
}
func boundaryBefore(s string, start int) bool {
	if start == 0 {
		return true
	}
	r, _ := utf8.DecodeLastRuneInString(s[:start])
	return !word(r)
}
func startWord(s, term string) bool { return strings.HasPrefix(s, term) && boundaryAfter(s, len(term)) }
func containsWord(s, term string) bool {
	for from := 0; from < len(s); {
		at := strings.Index(s[from:], term)
		if at < 0 {
			return false
		}
		at += from
		if boundaryBefore(s, at) && boundaryAfter(s, at+len(term)) {
			return true
		}
		from = at + 1
	}
	return false
}

var director = regexp.MustCompile(`^(?:director|directeur|directrice|direktor|direttore)(?: of |[ ,]+)`)

func Seniority(raw string) *string {
	if raw == "" {
		return nil
	}
	s := Normalize(raw)
	for _, rule := range prefixes {
		if rule.pattern.MatchString(s) {
			return ptr(rule.slug)
		}
	}
	if strings.HasPrefix(s, "lead ") && !strings.HasPrefix(s[5:], "generat") && !strings.HasPrefix(s[5:], "qualif") {
		return ptr("lead")
	}
	if startWord(s, "tech lead") || startWord(s, "team lead") {
		return ptr("lead")
	}
	if strings.HasPrefix(s, "head of ") || director.MatchString(s) || strings.HasPrefix(s, "vice president ") || strings.HasPrefix(s, "vp ") {
		return ptr("director")
	}
	for _, term := range []string{"ceo", "cto", "cfo", "coo", "cio", "ciso", "cmo", "cpo"} {
		if startWord(s, term) && !strings.HasPrefix(s, term+" advisor") {
			return ptr("executive")
		}
	}
	for _, term := range []string{"managing director", "geschaftsfuhrer", "geschaftsfuhrerin", "geschaeftsfuehrer"} {
		if startWord(s, term) {
			return ptr("executive")
		}
	}
	excluded := false
	for start := 0; start < len(s); {
		at := strings.Index(s[start:], "stage ")
		if at < 0 {
			break
		}
		at += start
		next, _ := utf8.DecodeRuneInString(s[at+6:])
		if unicode.IsDigit(next) {
			excluded = true
			break
		}
		start = at + 1
	}
	if !excluded {
		for _, term := range []string{"internship", "intern", "praktikum", "praktikant", "praktikantin", "werkstudent", "werkstudentin", "working student", "stagiaire", "stage", "alternance", "alternant", "apprendistato", "apprentice", "apprenticeship", "duales studium", "ausbildung", "lehre", "lehrling", "trainee"} {
			if containsWord(s, term) {
				return ptr("intern")
			}
		}
	}
	for _, term := range []string{"graduate program", "graduate scheme", "new grad"} {
		if containsWord(s, term) {
			return ptr("entry")
		}
	}
	return nil
}
