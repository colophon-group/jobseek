package dom

import (
	"errors"
	"net/url"
	"strconv"
	"strings"

	"golang.org/x/net/html"
)

var ErrTitleScan = errors.New("DOM complete title inventory could not be resolved")

type TitleScanConfig struct {
	ListingSelector, DetailSelector, Template string
	Start, MaxScan                            int
}

func titleScanOptions(raw any, c ListingConfig) (*TitleScanConfig, error) {
	if raw == nil {
		return nil, nil
	}
	m, err := object(raw)
	if err != nil {
		return nil, ErrTitleScan
	}
	for k := range m {
		if k != "listing_title_selector" && k != "detail_title_selector" && k != "url_template" && k != "start" && k != "max_scan" {
			return nil, ErrTitleScan
		}
	}
	s := &TitleScanConfig{}
	if s.ListingSelector, err = proofSelector(m["listing_title_selector"], true); err != nil {
		return nil, ErrTitleScan
	}
	if s.DetailSelector, err = proofSelector(m["detail_title_selector"], true); err != nil {
		return nil, ErrTitleScan
	}
	if s.Template, err = scriptText(m["url_template"], 2048, true); err != nil || strings.Count(s.Template, "{index}") != 1 {
		return nil, ErrTitleScan
	}
	for key, dest := range map[string]*int{"start": &s.Start, "max_scan": &s.MaxScan} {
		if _, bad := m[key].(bool); bad {
			return nil, ErrTitleScan
		}
		def := 1
		if key == "max_scan" {
			def = 20
		}
		*dest, err = number(m[key], def)
		if err != nil || *dest < 1 {
			return nil, ErrTitleScan
		}
	}
	if s.MaxScan > 500 || s.Start > int(^uint(0)>>1)-s.MaxScan {
		return nil, ErrTitleScan
	}
	if !scriptAbsoluteURL(s.URL(s.Start)) {
		return nil, ErrTitleScan
	}
	if c.Pagination != nil || c.RichRows != nil || c.ScriptLinks != nil || c.Selector != "" || c.OnclickSelector != "" || c.IncludeBoardURL || c.FetchURL != "" || c.EmptySelector != "" || c.EmptyText != "" || c.Proofs != nil {
		return nil, ErrTitleScan
	}
	return s, nil
}

func (s TitleScanConfig) URL(index int) string {
	return strings.Replace(s.Template, "{index}", strconv.Itoa(index), 1)
}

func (s TitleScanConfig) Candidates(board, include string) ([]string, error) {
	b, err := url.Parse(board)
	if err != nil {
		return nil, ErrTitleScan
	}
	re, err := CompileURLPattern(include)
	if err != nil {
		return nil, ErrTitleScan
	}
	origin := func(u *url.URL) string {
		p := u.Port()
		if p == "" {
			p = "80"
			if strings.EqualFold(u.Scheme, "https") {
				p = "443"
			}
		}
		return strings.ToLower(u.Scheme) + "://" + strings.ToLower(u.Hostname()) + ":" + p
	}
	out := make([]string, 0, s.MaxScan)
	for i := s.Start; i < s.Start+s.MaxScan; i++ {
		raw := s.URL(i)
		u, e := url.Parse(raw)
		if e != nil || u.User != nil || origin(u) != origin(b) {
			return nil, ErrTitleScan
		}
		if include != "" {
			ok, e := re.MatchString(raw)
			if e != nil || !ok {
				return nil, ErrTitleScan
			}
		}
		out = append(out, raw)
	}
	return out, nil
}

func titleScanTexts(source, selector string) ([]string, error) {
	tree, err := html.ParseWithOptions(strings.NewReader(source), html.ParseOptionEnableScripting(false))
	if err != nil {
		return nil, ErrTitleScan
	}
	out := []string{}
	for _, n := range proofNodes(tree, selector) {
		out = append(out, strings.Join(strings.FieldsFunc(richRowsText(n, " "), pySpace), " "))
	}
	return out, nil
}

func (s TitleScanConfig) Expected(source string) ([]string, error) {
	titles, err := titleScanTexts(source, s.ListingSelector)
	if err != nil || len(titles) == 0 || len(titles) > 500 || len(titles) > s.MaxScan {
		return nil, ErrTitleScan
	}
	seen := map[string]bool{}
	for _, t := range titles {
		if t == "" || seen[t] {
			return nil, ErrTitleScan
		}
		seen[t] = true
	}
	return titles, nil
}

func (s TitleScanConfig) DetailTitle(source string) (string, error) {
	titles, err := titleScanTexts(source, s.DetailSelector)
	if err != nil || len(titles) > 1 {
		return "", ErrTitleScan
	}
	if len(titles) == 0 {
		return "", nil
	}
	return titles[0], nil
}
