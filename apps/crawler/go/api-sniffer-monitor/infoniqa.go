package apisniffer

import (
	"io"
	"net/url"
	"regexp"
	"strconv"
	"strings"
	"unicode"
	"unicode/utf8"

	"golang.org/x/net/html"
)

type InfoniqaShell struct {
	CSRF         string `json:"csrf"`
	EmployerName string `json:"employer_name"`
}

type InfoniqaSearch struct {
	Total int      `json:"total"`
	URLs  []string `json:"urls"`
}

var infoniqaJobID = regexp.MustCompile(`^[0-9a-f]{32}$`)
var infoniqaCSRF = regexp.MustCompile(`^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$`)
var infoniqaLocation = regexp.MustCompile(`(?:^|[;{])\s*window\.location\.href\s*=\s*'([^']+)'\s*(?:;|\}|$)`)
var infoniqaButtonCount = regexp.MustCompile(`\$\('#showJobsButton'\)\.val\('[^'\r\n]*\(([0-9]+)\)'\)`)
var infoniqaDigits = regexp.MustCompile(`[0-9]+`)

func InfoniqaBoardURLs(source string) (string, string, error) {
	u, err := url.Parse(source)
	if err != nil || u.Scheme != "https" || u.User != nil || !strings.HasSuffix(strings.ToLower(u.Hostname()), ".infoniqa.io") || u.Port() != "" && u.Port() != "443" || u.Path != "/hcm/jobexchange/showJobOfferList.do" || u.Fragment != "" {
		return "", "", ErrOptions
	}
	q, err := url.ParseQuery(u.RawQuery)
	if err != nil || len(q) != 2 || len(q["init"]) != 1 || q.Get("init") != "true" || len(q["j"]) != 1 || q.Get("j") != "jobexchange" {
		return "", "", ErrOptions
	}
	origin := "https://" + strings.ToLower(u.Hostname())
	return origin, origin + u.Path, nil
}

func portalTokenAttrs(token html.Token) map[string]string {
	out := map[string]string{}
	for _, a := range token.Attr {
		out[a.Key] = a.Val
	}
	return out
}

func portalHasClass(value, class string) bool {
	for _, candidate := range strings.Fields(value) {
		if candidate == class {
			return true
		}
	}
	return false
}

// HTMLParser handles a self-closing tag as a start immediately followed by
// its end, including controls whose scope is otherwise left open until EOF.
func portalNextToken(z *html.Tokenizer, pending **html.Token) (html.TokenType, html.Token) {
	if *pending != nil {
		t := **pending
		*pending = nil
		return html.EndTagToken, t
	}
	kind := z.Next()
	t := z.Token()
	if kind == html.SelfClosingTagToken {
		*pending = &t
		kind = html.StartTagToken
	}
	return kind, t
}

func ParseInfoniqaShell(body []byte, source, expectedEmployer string) (InfoniqaShell, error) {
	origin, listing, err := InfoniqaBoardURLs(source)
	if err != nil || len(body) > 5_000_000 {
		return InfoniqaShell{}, ErrOptions
	}
	base, _ := url.Parse(origin + "/hcm/jobexchange/")
	bodies, headings, logos, quickLinks, forms, formIDs, tokens := 0, []string{}, []string{}, []string{}, [][2]string{}, []string{}, []string{}
	var heading, scripts strings.Builder
	inHeading, inForm, inScript := false, false, false
	z := html.NewTokenizer(strings.NewReader(string(body)))
	var pending *html.Token
	for {
		kind, t := portalNextToken(z, &pending)
		if kind == html.ErrorToken {
			if z.Err() != io.EOF {
				return InfoniqaShell{}, ErrInventory
			}
			break
		}
		a := portalTokenAttrs(t)
		switch kind {
		case html.StartTagToken, html.SelfClosingTagToken:
			switch t.Data {
			case "body":
				bodies++
				classes := map[string]bool{}
				for _, class := range strings.Fields(a["class"]) {
					classes[class] = true
				}
				if len(classes) != 1 || !classes["jobOfferList"] {
					return InfoniqaShell{}, ErrInventory
				}
			case "h1":
				if portalHasClass(a["class"], "caption") {
					if inHeading {
						return InfoniqaShell{}, ErrInventory
					}
					inHeading = true
					heading.Reset()
				}
			case "img":
				if a["alt"] != "" && strings.Contains(a["src"], "/styles/jobexchange/") {
					logos = append(logos, a["alt"])
				}
			case "a":
				if portalHasClass(a["class"], "menuQuicksearch") {
					u, err := base.Parse(a["href"])
					if err != nil {
						return InfoniqaShell{}, ErrInventory
					}
					quickLinks = append(quickLinks, u.String())
				}
			case "form":
				if a["id"] == "jobOfferSearch" {
					if inForm {
						return InfoniqaShell{}, ErrInventory
					}
					inForm = true
					forms = append(forms, [2]string{strings.ToLower(a["method"]), a["action"]})
				}
			case "input":
				if inForm {
					if a["name"] == "j" {
						formIDs = append(formIDs, a["value"])
					} else if a["name"] == "_csrf" {
						tokens = append(tokens, a["value"])
					}
				}
			case "script":
				inScript = true
			}
		case html.EndTagToken:
			if t.Data == "h1" && inHeading {
				headings = append(headings, inlineNormalized(heading.String()))
				inHeading = false
			} else if t.Data == "form" && inForm {
				inForm = false
			} else if t.Data == "script" {
				inScript = false
			}
		case html.TextToken:
			if inHeading {
				heading.WriteString(t.Data)
			}
			if inScript {
				scripts.WriteString(t.Data)
				scripts.WriteByte('\n')
			}
		}
	}
	if bodies != 1 || len(headings) != 1 || !strings.HasPrefix(headings[0], "Stellenangebote der ") {
		return InfoniqaShell{}, ErrInventory
	}
	employer := strings.TrimPrefix(headings[0], "Stellenangebote der ")
	if employer == "" || utf8.RuneCountInString(employer) > 200 || expectedEmployer != "" && employer != expectedEmployer || len(logos) != 1 || logos[0] != employer+" Logo" || len(quickLinks) != 1 || quickLinks[0] != listing+"?j=jobexchange" || len(forms) != 1 || forms[0] != [2]string{"post", ""} || len(formIDs) != 1 || formIDs[0] != "jobexchange" || len(tokens) != 1 || !infoniqaCSRF.MatchString(strings.ToLower(tokens[0])) {
		return InfoniqaShell{}, ErrInventory
	}
	for _, marker := range []string{"url: '/hcm/jobexchange/showJobOfferList.do?search=true'", "showNextJobOffers: 'true'", "hasNextJobOffers: 'true'"} {
		if !strings.Contains(scripts.String(), marker) {
			return InfoniqaShell{}, ErrInventory
		}
	}
	if strings.Count(scripts.String(), tokens[0]) < 3 {
		return InfoniqaShell{}, ErrInventory
	}
	return InfoniqaShell{CSRF: tokens[0], EmployerName: employer}, nil
}

func InfoniqaJobURL(raw, origin string) (string, error) {
	u, err := url.Parse(raw)
	if err != nil || u.Scheme != "" || u.Host != "" || u.Path != "showJobOfferDetail.do" || u.Fragment != "" {
		return "", ErrInventory
	}
	q, err := url.ParseQuery(u.RawQuery)
	if err != nil || len(q) != 3 || len(q["jobOfferId"]) != 1 || !infoniqaJobID.MatchString(q.Get("jobOfferId")) || len(q["j"]) != 1 || q.Get("j") != "jobexchange" || len(q["organizationUnitId"]) != 1 || q.Get("organizationUnitId") != "" {
		return "", ErrInventory
	}
	return origin + "/hcm/jobexchange/showJobOfferDetail.do?jobOfferId=" + q.Get("jobOfferId") + "&j=jobexchange&organizationUnitId=", nil
}

type infoniqaResultFacts struct {
	urls, titles, infos []string
	scripts             string
	jobLists, next      int
}

func parseInfoniqaRows(body []byte, source string) (infoniqaResultFacts, error) {
	origin, _, err := InfoniqaBoardURLs(source)
	if err != nil || len(body) > 5_000_000 {
		return infoniqaResultFacts{}, ErrOptions
	}
	f := infoniqaResultFacts{urls: []string{}}
	depth := 0
	jobURL := ""
	inTitle, inInfo, inScript := false, false, false
	var title, info, scripts strings.Builder
	z := html.NewTokenizer(strings.NewReader(string(body)))
	var pending *html.Token
	for {
		kind, t := portalNextToken(z, &pending)
		if kind == html.ErrorToken {
			if z.Err() != io.EOF {
				return f, ErrInventory
			}
			break
		}
		a := portalTokenAttrs(t)
		if kind == html.StartTagToken || kind == html.SelfClosingTagToken {
			if t.Data == "ul" && a["id"] == "jobOffers" {
				f.jobLists++
			} else if t.Data == "span" && a["id"] == "showNextJobOffers" {
				f.next++
			} else if t.Data == "p" && portalHasClass(a["class"], "searchResultInfo") {
				if inInfo {
					return f, ErrInventory
				}
				inInfo = true
				info.Reset()
			} else if t.Data == "script" {
				inScript = true
			}
			if t.Data == "li" && portalHasClass(a["class"], "jobOffer") {
				if depth != 0 {
					return f, ErrInventory
				}
				click, key := infoniqaLocation.FindAllStringSubmatch(a["onclick"], -1), infoniqaLocation.FindAllStringSubmatch(a["onkeydown"], -1)
				if len(click) != 1 || len(key) != 1 {
					return f, ErrInventory
				}
				jobURL, err = InfoniqaJobURL(click[0][1], origin)
				other, e := InfoniqaJobURL(key[0][1], origin)
				if err != nil || e != nil || other != jobURL {
					return f, ErrInventory
				}
				depth = 1
				inTitle = false
			} else if t.Data == "li" && depth != 0 {
				depth++
			} else if t.Data == "h2" && depth != 0 && portalHasClass(a["class"], "jobOfferDescription") {
				if inTitle {
					return f, ErrInventory
				}
				inTitle = true
				title.Reset()
			}
		} else if kind == html.EndTagToken {
			if t.Data == "h2" && inTitle {
				s := inlineNormalized(title.String())
				if s == "" || utf8.RuneCountInString(s) > 300 {
					return f, ErrInventory
				}
				f.titles = append(f.titles, s)
				inTitle = false
			} else if t.Data == "li" && depth != 0 {
				depth--
				if depth == 0 {
					f.urls = append(f.urls, jobURL)
					jobURL = ""
				}
			} else if t.Data == "p" && inInfo {
				f.infos = append(f.infos, inlineNormalized(info.String()))
				inInfo = false
			} else if t.Data == "script" {
				inScript = false
			}
		} else if kind == html.TextToken {
			if inTitle {
				title.WriteString(t.Data)
			}
			if inInfo {
				info.WriteString(t.Data)
			}
			if inScript {
				scripts.WriteString(t.Data)
				scripts.WriteByte('\n')
			}
		}
	}
	if depth != 0 || jobURL != "" || inTitle || len(f.urls) != len(f.titles) {
		return f, ErrInventory
	}
	f.scripts = scripts.String()
	return f, nil
}

func infoniqaResultCount(text string) []string {
	out := []string{}
	word := func(r rune) bool { return unicode.IsLetter(r) || unicode.IsNumber(r) || r == '_' }
	for _, bounds := range infoniqaDigits.FindAllStringIndex(text, -1) {
		before, after := false, false
		if bounds[0] != 0 {
			r, _ := utf8.DecodeLastRuneInString(text[:bounds[0]])
			before = word(r)
		}
		if bounds[1] != len(text) {
			r, _ := utf8.DecodeRuneInString(text[bounds[1]:])
			after = word(r)
		}
		if !before && !after {
			out = append(out, text[bounds[0]:bounds[1]])
		}
	}
	return out
}

func ParseInfoniqaSearch(body []byte, source string) (InfoniqaSearch, error) {
	f, err := parseInfoniqaRows(body, source)
	if err != nil || f.jobLists != 1 || f.next != 1 || len(f.infos) != 1 {
		return InfoniqaSearch{}, ErrInventory
	}
	info, button := infoniqaResultCount(f.infos[0]), infoniqaButtonCount.FindAllStringSubmatch(f.scripts, -1)
	if len(info) != 1 || len(button) != 1 || info[0] != button[0][1] {
		return InfoniqaSearch{}, ErrInventory
	}
	total, err := strconv.Atoi(info[0])
	if err != nil || total > 50_000 || len(f.urls) > total {
		return InfoniqaSearch{}, ErrInventory
	}
	seen := map[string]bool{}
	for _, u := range f.urls {
		if seen[u] {
			return InfoniqaSearch{}, ErrInventory
		}
		seen[u] = true
	}
	return InfoniqaSearch{Total: total, URLs: f.urls}, nil
}

func ParseInfoniqaPage(body []byte, source string) ([]string, error) {
	f, err := parseInfoniqaRows(body, source)
	if err != nil || f.jobLists != 0 || f.next != 0 || len(f.infos) != 0 || len(f.urls) == 0 {
		return nil, ErrInventory
	}
	seen := map[string]bool{}
	for _, u := range f.urls {
		if seen[u] {
			return nil, ErrInventory
		}
		seen[u] = true
	}
	return f.urls, nil
}
