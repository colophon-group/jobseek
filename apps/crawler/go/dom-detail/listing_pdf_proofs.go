package dom

import (
	"errors"
	"regexp"
	"strings"
	"time"

	"github.com/dlclark/regexp2/v2"
)

var ErrPDFProof = errors.New("PDF inventory classification failed")

type PDFDateRule struct {
	Pattern *regexp2.Regexp
	Format  string
}
type PDFListingProof struct {
	Deadlines                  []PDFDateRule
	Window                     *PDFDateRule
	Include, Exclude, Required *regexp2.Regexp
}

var pdfDateLayouts = map[string]string{"%d %B %Y": "2 January 2006", "%B %d, %Y": "January 2, 2006", "%B %d %Y": "January 2 2006", "%d of %B %Y": "2 of January 2006", "%Y-%m-%d": "2006-1-2", "%d.%m.%Y": "2.1.2006"}

func pdfProofPattern(raw any) (*regexp2.Regexp, error) {
	s, e := scriptText(raw, 1024, true)
	if e != nil {
		return nil, ErrPDFProof
	}
	r, e := CompileURLPattern(s)
	if e != nil {
		return nil, ErrPDFProof
	}
	return r, nil
}
func pdfDateRule(raw any) (PDFDateRule, error) {
	m, e := object(raw)
	if e != nil || len(m) != 2 {
		return PDFDateRule{}, ErrPDFProof
	}
	r, e := pdfProofPattern(m["pattern"])
	if e != nil || len(r.GetGroupNumbers()) < 2 {
		return PDFDateRule{}, ErrPDFProof
	}
	f, e := scriptText(m["date_format"], 128, true)
	if e != nil || pdfDateLayouts[f] == "" {
		return PDFDateRule{}, ErrPDFProof
	}
	return PDFDateRule{r, f}, nil
}

// Shared by original DOM and configured API monitors; this validates content
// controls only. Each worker binds the fetched candidate and transport afresh.
func PDFListingProofOptions(options Object) (*PDFListingProof, error) {
	if options["require_unexpired_pdf"] == nil && options["require_pdf_text"] == nil && options["require_pdf_pattern"] == nil {
		return nil, nil
	}
	p := &PDFListingProof{}
	if raw := options["require_unexpired_pdf"]; raw != nil {
		m, e := object(raw)
		if e != nil {
			return nil, ErrPDFProof
		}
		if m["pattern"] != nil || m["date_format"] != nil {
			r, e := pdfDateRule(m)
			if e != nil {
				return nil, e
			}
			p.Deadlines = []PDFDateRule{r}
		} else {
			for k := range m {
				if k != "rules" && k != "active_window" {
					return nil, ErrPDFProof
				}
			}
			if raw := m["rules"]; raw != nil {
				rows, ok := raw.([]any)
				if !ok || len(rows) > 16 {
					return nil, ErrPDFProof
				}
				for _, v := range rows {
					r, e := pdfDateRule(v)
					if e != nil {
						return nil, e
					}
					p.Deadlines = append(p.Deadlines, r)
				}
			}
			if raw := m["active_window"]; raw != nil {
				r, e := pdfDateRule(raw)
				if e != nil {
					return nil, e
				}
				names := map[string]bool{}
				for _, n := range r.Pattern.GetGroupNames() {
					names[n] = true
				}
				for _, n := range []string{"month", "opens", "closes", "year"} {
					if !names[n] {
						return nil, ErrPDFProof
					}
				}
				p.Window = &r
			}
			if len(p.Deadlines) == 0 && p.Window == nil {
				return nil, ErrPDFProof
			}
		}
	}
	if raw := options["require_pdf_text"]; raw != nil {
		m, e := object(raw)
		if e != nil || len(m) != 2 {
			return nil, ErrPDFProof
		}
		p.Include, e = pdfProofPattern(m["include"])
		if e != nil {
			return nil, e
		}
		p.Exclude, e = pdfProofPattern(m["exclude"])
		if e != nil {
			return nil, e
		}
	}
	if raw := options["require_pdf_pattern"]; raw != nil {
		if len(p.Deadlines) == 0 && p.Window == nil {
			return nil, ErrPDFProof
		}
		var e error
		p.Required, e = pdfProofPattern(raw)
		if e != nil {
			return nil, e
		}
	}
	return p, nil
}

var pdfOrdinal = regexp.MustCompile(`(?i)([0-9])(?:st|nd|rd|th)\b`)
var pdfGermanDay = regexp.MustCompile(`([0-9])\.(\s)`)
var pdfMonthWord = regexp.MustCompile(`(?i)\b(?:januar|februar|märz|mai|juni|juli|oktober|dezember)\b`)
var pdfMonthNames = map[string]string{"januar": "January", "februar": "February", "märz": "March", "mai": "May", "juni": "June", "juli": "July", "oktober": "October", "dezember": "December"}

func pdfProofDate(raw, format string) (time.Time, error) {
	raw = strings.Join(strings.FieldsFunc(raw, pySpace), " ")
	raw = pdfOrdinal.ReplaceAllString(raw, "${1}")
	raw = pdfGermanDay.ReplaceAllString(raw, "${1}${2}")
	raw = pdfMonthWord.ReplaceAllStringFunc(raw, func(v string) string { return pdfMonthNames[strings.ToLower(v)] })
	t, e := time.Parse(pdfDateLayouts[format], raw)
	if e != nil {
		return time.Time{}, ErrPDFProof
	}
	return t, nil
}

// Classify preserves exhaustive ownership and exact deadline cardinality.
// Window dates are inclusive in UTC; all-closed windows retain their latest
// parsed close date as evidence, while missing/ambiguous dates fail the cycle.
func (p *PDFListingProof) ClassifyCurrentness(text string, now time.Time) (keep bool, deadline string, err error) {
	if p == nil {
		return true, "", nil
	}
	if p.Required != nil {
		ok, e := p.Required.MatchString(text)
		if e != nil || !ok {
			return false, "", ErrPDFProof
		}
	}
	today := time.Date(now.UTC().Year(), now.UTC().Month(), now.UTC().Day(), 0, 0, 0, 0, time.UTC)
	keep = true
	if len(p.Deadlines) > 0 || p.Window != nil {
		var classified, active time.Time
		windowFound := false
		if p.Window != nil {
			m, e := p.Window.Pattern.FindStringMatch(text)
			if e != nil {
				return false, "", ErrPDFProof
			}
			for ; m != nil; m, e = p.Window.Pattern.FindNextMatch(m) {
				if e != nil {
					return false, "", ErrPDFProof
				}
				windowFound = true
				group := func(n string) (string, error) {
					g := m.GroupByName(n)
					if g == nil || len(g.Captures) == 0 {
						return "", ErrPDFProof
					}
					return g.String(), nil
				}
				month, e1 := group("month")
				year, e2 := group("year")
				opens, e3 := group("opens")
				closes, e4 := group("closes")
				if e1 != nil || e2 != nil || e3 != nil || e4 != nil {
					return false, "", ErrPDFProof
				}
				o, e1 := pdfProofDate(month+" "+opens+" "+year, p.Window.Format)
				c, e2 := pdfProofDate(month+" "+closes+" "+year, p.Window.Format)
				if e1 != nil || e2 != nil || c.Before(o) {
					return false, "", ErrPDFProof
				}
				if c.After(classified) {
					classified = c
				}
				if !today.Before(o) && !today.After(c) && c.After(active) {
					active = c
				}
			}
			if e != nil {
				return false, "", ErrPDFProof
			}
		}
		if windowFound {
			keep = !active.IsZero()
			if keep {
				classified = active
			}
			deadline = classified.Format("2006-01-02")
		} else {
			count := 0
			for _, r := range p.Deadlines {
				m, e := r.Pattern.FindStringMatch(text)
				if e != nil {
					return false, "", ErrPDFProof
				}
				for ; m != nil; m, e = r.Pattern.FindNextMatch(m) {
					if e != nil {
						return false, "", ErrPDFProof
					}
					count++
					if count > 1 {
						return false, "", ErrPDFProof
					}
					g := m.GroupByNumber(1)
					if g == nil || len(g.Captures) == 0 {
						return false, "", ErrPDFProof
					}
					classified, e = pdfProofDate(g.String(), r.Format)
					if e != nil {
						return false, "", e
					}
				}
				if e != nil {
					return false, "", ErrPDFProof
				}
			}
			if count != 1 {
				return false, "", ErrPDFProof
			}
			keep = !classified.Before(today)
			deadline = classified.Format("2006-01-02")
		}
	}
	return keep, deadline, nil
}

func (p *PDFListingProof) OwnershipKeeps(text string) (bool, error) {
	if p != nil && p.Include != nil {
		a, e1 := p.Include.MatchString(text)
		b, e2 := p.Exclude.MatchString(text)
		if e1 != nil || e2 != nil || a == b {
			return false, ErrPDFProof
		}
		return a, nil
	}
	return true, nil
}

func (p *PDFListingProof) Classify(text string, now time.Time) (bool, string, error) {
	keep, deadline, err := p.ClassifyCurrentness(text, now)
	if err != nil || !keep {
		return keep, deadline, err
	}
	keep, err = p.OwnershipKeeps(text)
	return keep, deadline, err
}
