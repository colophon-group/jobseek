package enrichment

import (
	_ "embed"
	"encoding/json"
	"fmt"
	"math"
	"math/big"
	"strconv"
	"strings"
	"time"
	"unicode"
	"unicode/utf8"

	rx "github.com/dlclark/regexp2/v2"
)

//go:embed salary_rules.json
var salaryRulesJSON []byte

type salaryPattern struct {
	Pattern    string `json:"pattern"`
	IgnoreCase bool   `json:"ignore_case"`
}
type salaryCurrency struct {
	Code       string  `json:"code"`
	RangeMin   float64 `json:"range_min"`
	RangeMax   float64 `json:"range_max"`
	MonthlyMin float64 `json:"monthly_min"`
	MonthlyMax float64 `json:"monthly_max"`
	HourlyMin  float64 `json:"hourly_min"`
	HourlyMax  float64 `json:"hourly_max"`
}
type salaryRules struct {
	Patterns   map[string]salaryPattern `json:"patterns"`
	Mojibake   [][2]string              `json:"mojibake"`
	Periods    [][2]string              `json:"periods"`
	Prefixes   map[string]string        `json:"prefixes"`
	Currencies []salaryCurrency         `json:"currencies"`
	Tokens     map[string][]string      `json:"tokens"`
}

var salaryConfig salaryRules
var salaryRegexes = map[string]*rx.Regexp{}

// Preserve Python's Unicode classes and boundary, rather than .NET's wider
// word class (which includes combining marks). The backtracking engine is
// pure Go; lookarounds preserve the existing ordered pattern alternatives.
func salaryRegex(pattern string, ci bool) *rx.Regexp {
	const ws = `\t\n\v\f\r \x1c-\x1f\x85\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000`
	const word = `\p{L}\p{N}_`
	const boundary = `(?:(?<![\p{L}\p{N}_])(?=[\p{L}\p{N}_])|(?<=[\p{L}\p{N}_])(?![\p{L}\p{N}_]))`
	var b strings.Builder
	inClass := false
	for i := 0; i < len(pattern); i++ {
		c := pattern[i]
		if c == '\\' && i+1 < len(pattern) {
			i++
			switch pattern[i] {
			case 'd':
				b.WriteString(`\p{Nd}`)
			case 'w':
				if inClass {
					b.WriteString(word)
				} else {
					b.WriteString("[" + word + "]")
				}
			case 's':
				if inClass {
					b.WriteString(ws)
				} else {
					b.WriteString("[" + ws + "]")
				}
			case 'b':
				if inClass {
					b.WriteString(`\x08`)
				} else {
					b.WriteString(boundary)
				}
			default:
				b.WriteByte('\\')
				b.WriteByte(pattern[i])
			}
		} else {
			if c == '[' {
				inClass = true
			}
			if c == ']' {
				inClass = false
			}
			b.WriteByte(c)
		}
	}
	opts := []rx.CompileOption{rx.OptionMaxBacktrackingStackSize(1 << 20), rx.OptionMaxCachedRuneBufferLength(65536)}
	if ci {
		opts = append(opts, rx.IgnoreCase)
	}
	re := rx.MustCompile(b.String(), opts...)
	re.MatchTimeout = 500 * time.Millisecond
	return re
}
func init() {
	if err := json.Unmarshal(salaryRulesJSON, &salaryConfig); err != nil {
		panic(err)
	}
	for name, p := range salaryConfig.Patterns {
		salaryRegexes[name] = salaryRegex(p.Pattern, p.IgnoreCase)
	}
}

type SalaryRange struct {
	Min      int64  `json:"min"`
	Max      *int64 `json:"max"`
	Currency string `json:"currency"`
	Period   string `json:"period"`
}
type ParsedSalary struct {
	Currency string `json:"currency"`
	Min      any    `json:"min"`
	Max      any    `json:"max"`
	Unit     string `json:"unit"`
}
type SalaryResult struct {
	Ranges  []SalaryRange `json:"ranges"`
	Unified *SalaryRange  `json:"unified"`
	Parsed  *ParsedSalary `json:"parsed"`
	EUR     *int64        `json:"eur"`
}
type salaryFailure struct{ reason string }

func salaryFail(reason string) { panic(salaryFailure{reason}) }

type salaryMatch struct {
	start, end int
	groups     []string
}
type salaryScan struct {
	text     []rune
	folded   []rune
	deadline time.Time
}

func newSalaryScan(text string, deadline time.Time) *salaryScan {
	r := []rune(text)
	f := append([]rune(nil), r...)
	for i, c := range f {
		switch c {
		case 'İ', 'ı':
			f[i] = 'i'
		case 'ſ':
			f[i] = 's'
		case 'K':
			f[i] = 'k'
		}
	}
	return &salaryScan{r, f, deadline}
}
func (s *salaryScan) check() {
	if time.Now().After(s.deadline) {
		salaryFail("salary extraction deadline exceeded")
	}
}
func (s *salaryScan) matches(name string, first bool) []salaryMatch {
	s.check()
	input := s.text
	if salaryConfig.Patterns[name].IgnoreCase {
		input = s.folded
	}
	m, err := salaryRegexes[name].FindRunesMatch(input)
	out := []salaryMatch{}
	for m != nil && err == nil {
		s.check()
		v := salaryMatch{start: m.RuneIndex, end: m.RuneIndex + m.RuneLength, groups: make([]string, m.GroupCount())}
		for i, g := range m.Groups() {
			if len(g.Captures) > 0 {
				v.groups[i] = string(s.text[g.RuneIndex : g.RuneIndex+g.RuneLength])
			}
		}
		out = append(out, v)
		if first {
			break
		}
		m, err = salaryRegexes[name].FindNextMatch(m)
	}
	if err != nil {
		salaryFail("salary regular expression limit exceeded")
	}
	return out
}
func (s *salaryScan) found(name string) bool { return len(s.matches(name, true)) > 0 }
func (s *salaryScan) window(m salaryMatch, before, after int) string {
	return string(s.text[max(0, m.start-before):min(len(s.text), m.end+after)])
}
func (s *salaryScan) has(name, text string) bool { return newSalaryScan(text, s.deadline).found(name) }
func (s *salaryScan) replace(name, text, replacement string) string {
	child := newSalaryScan(text, s.deadline)
	matches := child.matches(name, false)
	var b strings.Builder
	last := 0
	for _, m := range matches {
		b.WriteString(string(child.text[last:m.start]))
		b.WriteString(replacement)
		last = m.end
	}
	b.WriteString(string(child.text[last:]))
	return b.String()
}
func salaryText(html string) string {
	text := experienceTags.ReplaceAllString(html, " ")
	if strings.Contains(text, "Â") || strings.Contains(text, "â€") || strings.Contains(text, "Ã") {
		for _, pair := range salaryConfig.Mojibake {
			text = strings.ReplaceAll(text, pair[0], pair[1])
		}
	}
	return strings.TrimFunc(experienceSpaces.ReplaceAllString(text, " "), space)
}
func salaryPeriod(text string) string {
	text = lower(strings.TrimFunc(text, space))
	for _, p := range salaryConfig.Periods {
		if strings.HasPrefix(text, p[0]) {
			return p[1]
		}
	}
	return ""
}
func salaryContains(text string, tokens ...string) bool {
	for _, t := range tokens {
		if strings.Contains(text, t) {
			return true
		}
	}
	return false
}
func salaryNumber(text string) (float64, bool) {
	var b strings.Builder
	for _, r := range text {
		if unicode.IsDigit(r) {
			b.WriteByte(byte('0' + decimalDigit(r)))
		} else {
			b.WriteRune(r)
		}
	}
	v, err := strconv.ParseFloat(b.String(), 64)
	// Python accepts overflowing finite literals as infinity; emission then
	// fails explicitly. Do not silently turn them into a missing salary.
	if n, ok := err.(*strconv.NumError); ok && n.Err == strconv.ErrRange {
		return v, true
	}
	return v, err == nil
}
func basicSalaryNumber(text string) (float64, bool) {
	text = strings.NewReplacer(",", "", " ", "", "'", "", "’", "").Replace(text)
	text = strings.TrimRight(text, ".")
	if strings.HasSuffix(strings.ToUpper(text), "K") {
		v, ok := salaryNumber(text[:len(text)-1])
		return v * 1000, ok
	}
	return salaryNumber(text)
}
func (s *salaryScan) dollarNumber(text, currency string) (float64, bool) {
	text = strings.TrimFunc(text, space)
	eu := strings.Contains(text, ",") && strings.Contains(text, ".") && strings.LastIndex(text, ",") > strings.LastIndex(text, ".")
	if !strings.Contains(text, ",") && strings.Contains(text, ".") {
		eu = s.has("_is_european_decimal:fullmatch", text)
	}
	if currency == "BRL" && eu {
		text = strings.ReplaceAll(strings.ReplaceAll(text, ".", ""), ",", ".")
		if strings.HasSuffix(strings.ToUpper(text), "K") {
			v, ok := salaryNumber(text[:len(text)-1])
			return v * 1000, ok
		}
		return salaryNumber(text)
	}
	return basicSalaryNumber(text)
}
func (s *salaryScan) euNumber(raw string, eur bool) (float64, bool) {
	text := strings.TrimFunc(raw, space)
	if !eur {
		text = s.replace("_parse_eu_number:sub", text, "")
	}
	if text == "" {
		return 0, false
	}
	text = strings.TrimRight(text, ".")
	text = strings.NewReplacer("\u00a0", " ", "\u202f", " ", "\u2009", " ").Replace(text)
	if eur {
		text = strings.NewReplacer("'", "", "’", "").Replace(text)
	}
	comma, dot, spaces := strings.Contains(text, ","), strings.Contains(text, "."), strings.Contains(text, " ")
	if !eur {
		text = strings.NewReplacer("'", "", "’", "").Replace(text)
	}
	switch {
	case comma && dot:
		if strings.LastIndex(text, ",") > strings.LastIndex(text, ".") {
			text = strings.ReplaceAll(strings.ReplaceAll(text, ".", ""), ",", ".")
		} else {
			text = strings.ReplaceAll(text, ",", "")
		}
	case comma:
		after := strings.Split(text, ",")
		if utf8.RuneCountInString(after[len(after)-1]) == 3 && !spaces {
			text = strings.ReplaceAll(text, ",", "")
		} else {
			text = strings.ReplaceAll(text, ",", ".")
		}
	case dot:
		after := strings.Split(text, ".")
		if len(after) > 2 || (utf8.RuneCountInString(after[len(after)-1]) == 3 && utf8.RuneCountInString(strings.ReplaceAll(text, ".", "")) > 3) {
			text = strings.ReplaceAll(text, ".", "")
		}
	}
	if spaces {
		text = strings.ReplaceAll(text, " ", "")
	}
	if eur && strings.HasSuffix(strings.ToUpper(text), "K") {
		v, ok := salaryNumber(text[:len(text)-1])
		return v * 1000, ok
	}
	return salaryNumber(text)
}
func (s *salaryScan) currency(prefix, tail string) string {
	if v := salaryConfig.Prefixes[strings.ToUpper(prefix)]; v != "" {
		return v
	}
	if m := newSalaryScan(tail, s.deadline).matches("_SUFFIX_CURRENCY_RE", true); len(m) > 0 {
		return strings.ToUpper(m[0].groups[1])
	}
	return "USD"
}
func salaryInteger(v float64) int64 {
	if math.IsNaN(v) || math.IsInf(v, 0) || v >= math.Exp2(63) || v < -math.Exp2(63) {
		salaryFail("salary integer is outside transport range")
	}
	return int64(v)
}
func salaryEmit(lo float64, hi *float64, currency, period string, scaleHourly bool) SalaryRange {
	scale := 1.0
	if scaleHourly && period == "hourly" {
		scale = 100
	}
	out := SalaryRange{Min: salaryInteger(lo * scale), Currency: currency, Period: period}
	if hi != nil {
		v := salaryInteger(*hi * scale)
		out.Max = &v
	}
	return out
}
func salaryInside(m salaryMatch, spans []salaryMatch) bool {
	for _, p := range spans {
		if p.start <= m.start && m.end <= p.end {
			return true
		}
	}
	return false
}
func (s *salaryScan) structured() []SalaryRange {
	out := []SalaryRange{}
	for _, m := range s.matches("_LOCATION_SALARY_RE", false) {
		lo, ok := basicSalaryNumber(m.groups[1])
		hi, hOK := basicSalaryNumber(m.groups[2])
		if !ok || !hOK {
			salaryFail("invalid structured salary number")
		}
		period := "hourly"
		if lower(m.groups[4]) == "annually" {
			period = "yearly"
		}
		out = append(out, salaryEmit(lo, &hi, strings.ToUpper(m.groups[3]), period, true))
	}
	return out
}
func (s *salaryScan) bare() []SalaryRange {
	out := []SalaryRange{}
	for _, m := range s.matches("_BARE_RANGE_CURRENCY_RE", false) {
		g := m.groups
		a, b, c, p := g[1], g[2], strings.ToUpper(g[3]), g[4]
		if a == "" {
			a, b, c, p = g[5], g[6], "USD", "yr"
		}
		lo, ok := basicSalaryNumber(a)
		hi, hOK := basicSalaryNumber(b)
		if !ok || !hOK {
			salaryFail("invalid bare salary number")
		}
		if (lo < 10000 || hi > 1000000) && !strings.Contains(lower(p), "hour") {
			continue
		}
		period := salaryPeriod(p)
		if period == "" {
			period = "yearly"
		}
		out = append(out, salaryEmit(lo, &hi, c, period, true))
	}
	return out
}
func (s *salaryScan) dollars() []SalaryRange {
	out := []SalaryRange{}
	spans := s.matches("_DOLLAR_RANGE_RE", false)
	for _, m := range spans {
		g := m.groups
		c := s.currency(g[1], g[4])
		lo, ok := s.dollarNumber(g[2], c)
		hi, hOK := s.dollarNumber(g[3], c)
		if !ok || !hOK || lo < 15000 || hi > 1000000 {
			continue
		}
		window := s.window(m, 100, 0) + g[4]
		if s.has("_NOT_SALARY_RE", window) || !s.has("_SALARY_CONTEXT_RE", window) {
			continue
		}
		period := salaryPeriod(strings.TrimLeftFunc(strings.TrimLeft(g[4], " +"), space))
		if period == "" {
			tail := s.replace("_extract_dollar_range:sub", g[4], "")
			period = salaryPeriod(strings.TrimLeftFunc(strings.TrimLeft(tail, " +.,"), space))
		}
		if period == "" {
			period = "yearly"
		}
		// Existing dollar-range rule stores whole units even with an hourly label.
		out = append(out, salaryEmit(lo, &hi, c, period, false))
	}
	for _, m := range s.matches("_SINGLE_DOLLAR_PERIOD_RE", false) {
		g := m.groups
		c := s.currency(g[1], g[3])
		v, ok := s.dollarNumber(g[2], c)
		if !ok {
			continue
		}
		p := salaryPeriod(s.replace("_extract_single_dollar:sub", g[3], ""))
		if p == "" || p == "yearly" && v < 15000 || p == "monthly" && (v < 500 || v > 100000) || p == "hourly" && (v < 7 || v > 500) {
			continue
		}
		out = append(out, salaryEmit(v, nil, c, p, true))
	}
	for _, m := range s.matches("_PREFIX_DOLLAR_SINGLE_RE", false) {
		if salaryInside(m, spans) {
			continue
		}
		c := s.currency(m.groups[1], "")
		if c == "USD" {
			continue
		}
		v, ok := s.dollarNumber(m.groups[2], c)
		if !ok {
			continue
		}
		window := s.window(m, 100, 80)
		if s.has("_NOT_SALARY_RE", window) || !s.has("_SALARY_CONTEXT_RE", window) {
			continue
		}
		tail := string(s.text[m.end:min(len(s.text), m.end+30)])
		p := salaryPeriod(strings.TrimLeftFunc(strings.TrimLeft(tail, " +.,/"), space))
		if p == "" {
			switch {
			case v >= 15000:
				p = "yearly"
			case v >= 500:
				p = "monthly"
			case v >= 5:
				p = "hourly"
			default:
				continue
			}
		}
		if p == "yearly" && v < 15000 || p == "monthly" && (v < 500 || v > 100000) || p == "hourly" && (v < 5 || v > 500) {
			continue
		}
		out = append(out, salaryEmit(v, nil, c, p, true))
	}
	return out
}
func (s *salaryScan) periodInWindow(rule, window string) string {
	m := newSalaryScan(window, s.deadline).matches(rule, true)
	if len(m) == 0 {
		return ""
	}
	raw := lower(strings.TrimFunc(m[0].groups[0], space))
	switch rule {
	case "_eur_period_in_window:search":
		if salaryContains(raw, "hour", "/hr", "stünd", "stunde", "heure", "/std") {
			return "hourly"
		}
		if salaryContains(raw, "month", "monat", "mensuel", "mensile", "mois", "maand", "14mal", "14 mal") {
			return "monthly"
		}
		if salaryContains(raw, "year", "annual", "annuel", "annuale", "jähr", "jahr", "annum", "p.a.", "an ", " an", "annuels", "annuel", "jaar", "pro rata") {
			return "yearly"
		}
	case "_gbp_period_from_window:search":
		if salaryContains(raw, "hour", "/hr") {
			return "hourly"
		}
		return "yearly"
	case "_extract_chf:search":
		if salaryContains(raw, "stunde", "hour", "heure") {
			return "hourly"
		}
		if salaryContains(raw, "monat", "month", "mois") {
			return "monthly"
		}
		if salaryContains(raw, "jahr", "year", "an") {
			return "yearly"
		}
	case "_EU_PERIOD_RE":
		for _, p := range []string{"hourly", "monthly", "yearly"} {
			if salaryContains(raw, salaryConfig.Tokens[p+"_tokens"]...) {
				return p
			}
		}
	}
	return ""
}
func eurSalaryValid(v float64, p string) bool {
	switch p {
	case "hourly":
		return v >= 5 && v <= 200
	case "monthly":
		return v >= 600 && v <= 30000
	case "yearly":
		return v >= 10000 && v <= 500000
	}
	return false
}
func (s *salaryScan) eurContext(w string) bool {
	return s.has("_EUR_CONTEXT_RE", w) && !s.has("_EUR_DISQUALIFY_RE", w) && !(s.has("_EUR_NET_RE", w) && !s.has("_EUR_GROSS_RE", w))
}
func (s *salaryScan) euros() []SalaryRange {
	out := []SalaryRange{}
	spans := []salaryMatch{}
	for _, m := range s.matches("_EUR_RANGE_RE", false) {
		a, b := "", ""
		for _, i := range []int{1, 3, 5} {
			if m.groups[i] != "" && m.groups[i+1] != "" {
				a, b = m.groups[i], m.groups[i+1]
				break
			}
		}
		if a == "" {
			continue
		}
		lo, ok := s.euNumber(a, true)
		hi, hOK := s.euNumber(b, true)
		if !ok || !hOK || hi < lo {
			continue
		}
		w := s.window(m, 200, 100)
		if !s.eurContext(w) {
			continue
		}
		p := s.periodInWindow("_eur_period_in_window:search", w)
		if p == "" {
			switch {
			case lo < 1000:
				p = "hourly"
			case lo < 10000:
				p = "monthly"
			default:
				p = "yearly"
			}
		}
		if !eurSalaryValid(lo, p) || !eurSalaryValid(hi, p) {
			continue
		}
		out = append(out, salaryEmit(lo, &hi, "EUR", p, true))
		spans = append(spans, m)
	}
	for _, m := range s.matches("_EUR_SINGLE_RE", false) {
		if salaryInside(m, spans) {
			continue
		}
		raw := ""
		for _, g := range m.groups[1:] {
			if g != "" {
				raw = g
				break
			}
		}
		if raw == "" {
			continue
		}
		w := s.window(m, 200, 100)
		if !s.eurContext(w) {
			continue
		}
		v, ok := s.euNumber(raw, true)
		if !ok {
			continue
		}
		p := s.periodInWindow("_eur_period_in_window:search", w)
		if p == "" {
			if v < 800 {
				continue
			}
			if v < 10000 {
				p = "monthly"
			} else {
				p = "yearly"
			}
		}
		if eurSalaryValid(v, p) {
			out = append(out, salaryEmit(v, nil, "EUR", p, true))
		}
	}
	return out
}
func (s *salaryScan) pounds() []SalaryRange {
	out := []SalaryRange{}
	spans := []salaryMatch{}
	for _, m := range s.matches("_GBP_RANGE_RE", false) {
		lo, ok := basicSalaryNumber(m.groups[1])
		hi, hOK := basicSalaryNumber(m.groups[2])
		if !ok || !hOK {
			salaryFail("invalid pound salary number")
		}
		w := s.window(m, 100, 0) + m.groups[3]
		if s.has("_GBP_NOT_SALARY_RE", w) || !s.has("_extract_gbp:search", w) {
			continue
		}
		p := s.periodInWindow("_gbp_period_from_window:search", w)
		if p == "" {
			if lo < 10000 || hi > 500000 {
				if lo < 7 || lo > 200 {
					continue
				}
				p = "hourly"
			} else {
				p = "yearly"
			}
		}
		if hi < lo || p == "hourly" && (lo < 5 || lo > 200) || p == "yearly" && (lo < 10000 || hi > 500000) {
			continue
		}
		out = append(out, salaryEmit(lo, &hi, "GBP", p, true))
		spans = append(spans, m)
	}
	for _, m := range s.matches("_GBP_SINGLE_RE", false) {
		if salaryInside(m, spans) {
			continue
		}
		v, ok := basicSalaryNumber(m.groups[1])
		if !ok {
			salaryFail("invalid pound salary number")
		}
		p := salaryPeriod(strings.ReplaceAll(lower(m.groups[2]), "an hour", "per hour"))
		if p == "" || p == "hourly" && (v < 5 || v > 200) || p == "yearly" && (v < 10000 || v > 500000) {
			continue
		}
		out = append(out, salaryEmit(v, nil, "GBP", p, true))
	}
	for _, m := range s.matches("_GBP_PREFIX_PERIOD_RE", false) {
		if salaryInside(m, spans) {
			continue
		}
		v, ok := basicSalaryNumber(m.groups[2])
		if !ok {
			salaryFail("invalid pound salary number")
		}
		if v < 5 || v > 200 {
			continue
		}
		out = append(out, salaryEmit(v, nil, "GBP", "hourly", true))
	}
	return out
}
func (s *salaryScan) francs() []SalaryRange {
	out := []SalaryRange{}
	for _, m := range s.matches("_CHF_RE", false) {
		w := s.window(m, 150, 100)
		if !s.has("_CHF_CONTEXT_RE", w) {
			continue
		}
		a := s.replace("_normalize_chf_amount:sub", strings.TrimFunc(m.groups[1], space), ".00")
		lo, ok := basicSalaryNumber(a)
		if !ok {
			continue
		}
		var hi *float64
		if m.groups[2] != "" {
			b := s.replace("_normalize_chf_amount:sub", strings.TrimFunc(m.groups[2], space), ".00")
			v, hOK := basicSalaryNumber(b)
			if !hOK {
				continue
			}
			if v != 0 {
				hi = &v
			}
		}
		p := s.periodInWindow("_extract_chf:search", w)
		if p == "" {
			switch {
			case lo < 500:
				p = "hourly"
			case lo < 15000:
				p = "monthly"
			default:
				p = "yearly"
			}
		}
		if p == "hourly" && (lo < 15 || lo > 300) || p == "monthly" && (lo < 1200 || lo > 30000) || p == "yearly" && (lo < 30000 || lo > 500000) {
			continue
		}
		out = append(out, salaryEmit(lo, hi, "CHF", p, true))
	}
	return out
}
func (s *salaryScan) extraEU() []SalaryRange {
	out := []SalaryRange{}
	for _, c := range salaryConfig.Currencies {
		for _, m := range s.matches(c.Code, false) {
			g := m.groups
			a, b := g[7], ""
			for _, i := range []int{1, 3, 5} {
				if g[i] != "" {
					a, b = g[i], g[i+1]
					break
				}
			}
			if a == "" {
				continue
			}
			lo, ok := s.euNumber(a, false)
			if !ok {
				continue
			}
			var hi *float64
			if b != "" {
				v, hOK := s.euNumber(b, false)
				if !hOK {
					continue
				}
				hi = &v
			}
			w := s.window(m, 200, 200)
			if !s.has("_EU_SALARY_CONTEXT_RE", w) || s.has("_EU_PERK_RE", w) || s.has("_EU_REVENUE_RE", w) || s.has("_EU_NET_RE", w) && !s.has("_EU_GROSS_RE", w) {
				continue
			}
			p := s.periodInWindow("_EU_PERIOD_RE", w)
			if p == "" {
				if lo >= c.RangeMin {
					p = "yearly"
				} else if lo >= c.MonthlyMin {
					p = "monthly"
				} else {
					continue
				}
			}
			floor, ceiling := c.RangeMin, c.RangeMax
			if p == "monthly" {
				floor, ceiling = c.MonthlyMin, c.MonthlyMax
			}
			if p == "hourly" {
				floor, ceiling = c.HourlyMin, c.HourlyMax
			}
			if lo < floor || lo > ceiling || hi != nil && (*hi < lo || *hi > ceiling*1.2) {
				continue
			}
			out = append(out, salaryEmit(lo, hi, c.Code, p, true))
		}
	}
	return out
}
func salaryUnify(ranges []SalaryRange) *SalaryRange {
	type key struct{ currency, period string }
	groups := map[key][]SalaryRange{}
	order := []key{}
	for _, r := range ranges {
		k := key{r.Currency, r.Period}
		if _, ok := groups[k]; !ok {
			order = append(order, k)
		}
		groups[k] = append(groups[k], r)
	}
	var best []SalaryRange
	for _, k := range order {
		if len(groups[k]) > len(best) {
			best = groups[k]
		}
	}
	if len(best) == 0 {
		return nil
	}
	out := best[0]
	// Copy the pointed-to maximum before widening the result.
	out.Max = nil
	for _, r := range best {
		out.Min = min(out.Min, r.Min)
		if r.Max != nil && (out.Max == nil || *r.Max > *out.Max) {
			v := *r.Max
			out.Max = &v
		}
	}
	return &out
}
func salaryDeduplicate(ranges []SalaryRange) []SalaryRange {
	if len(ranges) <= 1 {
		return ranges
	}
	out := []SalaryRange{}
	values := map[int64]bool{}
	for _, r := range ranges {
		if r.Max != nil {
			out = append(out, r)
			values[r.Min] = true
			values[*r.Max] = true
		}
	}
	type key struct {
		currency, period string
		value            int64
	}
	seen := map[key]bool{}
	for _, r := range ranges {
		if r.Max != nil || values[r.Min] {
			continue
		}
		k := key{r.Currency, r.Period, r.Min}
		if !seen[k] {
			out = append(out, r)
			seen[k] = true
		}
	}
	return out
}

// Salary owns all extraction families, ordered aggregation, public parsing and
// annual EUR conversion. Failures return an error, never a partial result or a
// Python fallback. Ranges use the signed 64-bit transport bound; persistence keeps its schema.
func Salary(html string, rates map[string]float64) (result *SalaryResult, err error) {
	defer func() {
		if v := recover(); v != nil {
			if e, ok := v.(salaryFailure); ok {
				result = nil
				err = fmt.Errorf("%s", e.reason)
			} else {
				panic(v)
			}
		}
	}()
	s := newSalaryScan(salaryText(html), time.Now().Add(5*time.Second))
	ranges := s.structured()
	if len(ranges) == 0 {
		ranges = s.bare()
	}
	if len(ranges) == 0 {
		ranges = append(ranges, s.dollars()...)
		ranges = append(ranges, s.euros()...)
		ranges = append(ranges, s.pounds()...)
		ranges = append(ranges, s.francs()...)
		ranges = append(ranges, s.extraEU()...)
		ranges = salaryDeduplicate(ranges)
	}
	result = &SalaryResult{Ranges: ranges, Unified: salaryUnify(ranges)}
	if sr := result.Unified; sr != nil {
		minValue, maxValue := any(sr.Min), any(nil)
		if sr.Max != nil {
			maxValue = *sr.Max
		}
		unit := "year"
		if sr.Period == "monthly" {
			unit = "month"
		}
		if sr.Period == "hourly" {
			unit = "hour"
			minValue = float64(sr.Min) / 100
			if sr.Max != nil {
				maxValue = float64(*sr.Max) / 100
			}
		}
		result.Parsed = &ParsedSalary{sr.Currency, minValue, maxValue, unit}
		if rate := rates[sr.Currency]; rate > 0 {
			annual := float64(sr.Min)
			switch sr.Period {
			case "hourly":
				perHour := float64(sr.Min) / 100
				annual = math.RoundToEven(perHour * 2080)
			case "monthly":
				// Python multiplies integers before converting to float for
				// the exchange rate. Keep the unbounded intermediate exact.
				integer := new(big.Int).Mul(big.NewInt(sr.Min), big.NewInt(12))
				annual, _ = new(big.Float).SetInt(integer).Float64()
			}
			eur := salaryInteger(math.RoundToEven(annual * rate))
			result.EUR = &eur
		}
	}
	return result, nil
}
