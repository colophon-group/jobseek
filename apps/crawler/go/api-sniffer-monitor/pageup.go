package apisniffer

import (
	"encoding/json"
	stdhtml "html"
	"io"
	"net/url"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"unicode"
	"unicode/utf8"

	"golang.org/x/net/html"
	"golang.org/x/text/cases"
)

type PageUpBoard struct {
	Instance int    `json:"instance"`
	Pointer  string `json:"source_pointer"`
	Locale   string `json:"locale"`
}

type PageUpPage struct {
	Jobs    []map[string]any
	Total   int
	HasNext bool
}

var pageupInstance = regexp.MustCompile(`^[1-9][0-9]{0,8}$`)
var pageupPointer = regexp.MustCompile(`^[a-z][a-z0-9-]{0,15}$`)
var pageupLocale = regexp.MustCompile(`^[a-z]{2,3}(?:-[a-z0-9]{2,8})*$`)
var pageupJobID = regexp.MustCompile(`^[1-9][0-9]{0,18}$`)
var pageupSourceAssignment = regexp.MustCompile(`\bPU\.Jobs\.source\s*=\s*`)
var pageupRemaining = regexp.MustCompile(`[0-9][0-9,]*`)

func (b PageUpBoard) ListingURL() string {
	return fmtPageupInstance(b.Instance) + "/" + b.Pointer + "/" + b.Locale
}

func fmtPageupInstance(instance int) string {
	return "https://careers.pageuppeople.com/" + strconv.Itoa(instance)
}

func (b PageUpBoard) PageURL(page, size int) string {
	return b.ListingURL() + "/listing/?page=" + strconv.Itoa(page) + "&page-items=" + strconv.Itoa(size)
}

func pageupBoardValues(instance any, pointer, locale any) (PageUpBoard, bool) {
	raw := ""
	switch v := instance.(type) {
	case string:
		raw = inlineTrim(v)
	case json.Number:
		raw = string(v)
	case int:
		raw = strconv.Itoa(v)
	}
	p, l := cases.Fold().String(smallText(pointer)), cases.Fold().String(smallText(locale))
	if !pageupInstance.MatchString(raw) || !pageupPointer.MatchString(p) || !pageupLocale.MatchString(l) {
		return PageUpBoard{}, false
	}
	n, err := strconv.Atoi(raw)
	return PageUpBoard{Instance: n, Pointer: p, Locale: l}, err == nil
}

func pageupURLParts(source string) (*url.URL, []string, url.Values, bool) {
	if len(source) > 4096 {
		return nil, nil, nil, false
	}
	u, err := url.Parse(stdhtml.UnescapeString(source))
	if err != nil || !strings.EqualFold(u.Scheme, "https") || strings.TrimSuffix(strings.ToLower(u.Hostname()), ".") != "careers.pageuppeople.com" || u.User != nil || u.Port() != "" && u.Port() != "443" || u.Fragment != "" {
		return nil, nil, nil, false
	}
	parts := strings.FieldsFunc(u.EscapedPath(), func(r rune) bool { return r == '/' })
	q, err := url.ParseQuery(u.RawQuery)
	if err != nil {
		return nil, nil, nil, false
	}
	for _, values := range q {
		if len(values) != 1 {
			return nil, nil, nil, false
		}
	}
	return u, parts, q, true
}

func pageupSlug(raw string) (string, bool) {
	s := cases.Fold().String(enterpriseUnquote(raw))
	if utf8.RuneCountInString(s) < 1 || utf8.RuneCountInString(s) > 200 {
		return "", false
	}
	for n, r := range s {
		alnum := unicode.IsLetter(r) || unicode.IsNumber(r)
		if n == 0 && !alnum || !alnum && r != '-' && r != '_' {
			return "", false
		}
	}
	return s, true
}

func PageUpBoardFromURL(source string) (PageUpBoard, bool) {
	_, parts, q, ok := pageupURLParts(source)
	if !ok || len(parts) < 3 {
		return PageUpBoard{}, false
	}
	b, ok := pageupBoardValues(parts[0], parts[1], parts[2])
	if !ok {
		return b, false
	}
	switch {
	case len(parts) == 3:
		return b, len(q) == 0
	case len(parts) == 4 && strings.EqualFold(parts[3], "listing"):
		if len(q) != 0 && (len(q) != 2 || q.Get("page") == "" || q.Get("page-items") == "") {
			return b, false
		}
		for _, values := range q {
			if !smallUnsigned.MatchString(values[0]) {
				return b, false
			}
			n, err := strconv.Atoi(values[0])
			if err != nil || n < 1 {
				return b, false
			}
		}
		return b, true
	case len(parts) == 6 && strings.EqualFold(parts[3], "job") && pageupJobID.MatchString(parts[4]) && len(q) == 0:
		_, valid := pageupSlug(parts[5])
		return b, valid
	}
	return b, false
}

func PageUpBoardFromMetadata(metadata map[string]any) (PageUpBoard, bool) {
	explicit, valid := pageupBoardValues(metadata["instance"], metadata["source_pointer"], metadata["locale"])
	has := false
	for _, key := range []string{"instance", "source_pointer", "locale"} {
		if _, exists := metadata[key]; exists {
			has = true
		}
	}
	if has && !valid {
		return PageUpBoard{}, false
	}
	if raw, exists := metadata["listing_url"]; exists {
		s, ok := raw.(string)
		listed, good := PageUpBoardFromURL(s)
		if !ok || !good || has && explicit != listed {
			return PageUpBoard{}, false
		}
		return listed, true
	}
	return explicit, valid
}

func PageUpJobURL(source string, board PageUpBoard) (string, bool) {
	b, ok := PageUpBoardFromURL(source)
	_, parts, _, valid := pageupURLParts(source)
	if !ok || !valid || b != board || len(parts) != 6 {
		return "", false
	}
	slug, ok := pageupSlug(parts[5])
	if !ok {
		return "", false
	}
	encoded := strings.TrimPrefix(TurboHirePublicURL("", slug), "/job/publicjobs/")
	return board.ListingURL() + "/job/" + parts[4] + "/" + encoded, true
}

func pageupPageIdentity(source string, board PageUpBoard) ([2]int, bool) {
	b, ok := PageUpBoardFromURL(source)
	_, parts, q, valid := pageupURLParts(source)
	if !ok || !valid || b != board || len(parts) != 4 || len(q) != 2 {
		return [2]int{}, false
	}
	page, e := strconv.Atoi(q.Get("page"))
	size, f := strconv.Atoi(q.Get("page-items"))
	return [2]int{page, size}, e == nil && f == nil
}

func pageupAssertions(body string) map[PageUpBoard]bool {
	out := map[PageUpBoard]bool{}
	for _, match := range pageupSourceAssignment.FindAllStringIndex(body, -1) {
		decoder := json.NewDecoder(strings.NewReader(body[match[1]:]))
		decoder.UseNumber()
		var value any
		if decoder.Decode(&value) != nil {
			continue
		}
		r, ok := value.(map[string]any)
		if !ok || r["baseDomain"] != "https://careers.pageuppeople.com" || r["action"] != "Listing" {
			continue
		}
		if b, ok := pageupBoardValues(r["instId"], r["sourcePointer"], r["language"]); ok {
			out[b] = true
		}
	}
	return out
}

func ParsePageUpListing(body []byte, request string, board PageUpBoard, page, size int, expectedTotal *int) (PageUpPage, error) {
	fail := func() (PageUpPage, error) { return PageUpPage{}, ErrInventory }
	if len(body) > 20_000_000 || utf8.RuneCount(body) > 5_000_000 || page < 1 || page > 100 || size < 1 || size > 50_000 {
		return fail()
	}
	assertions := pageupAssertions(string(body))
	if len(assertions) > 0 && (len(assertions) != 1 || !assertions[board]) {
		return fail()
	}
	titles, rawURLs := map[string]string{}, map[string]bool{}
	totals, next := map[int]bool{}, map[[2]int]bool{}
	remaining := map[[2]int]map[int]bool{}
	jobHref, jobActive, moreActive, inTotal := "", false, false, false
	var more [2]int
	var jobText, moreText, totalText strings.Builder
	generic := func(s string) bool {
		switch cases.Fold().String(s) {
		case "apply", "apply now", "learn more", "read more", "view details", "view job", "view position":
			return true
		}
		return false
	}
	z := html.NewTokenizer(strings.NewReader(string(body)))
	var pending *html.Token
	for {
		kind, t := portalNextToken(z, &pending)
		if kind == html.ErrorToken {
			if z.Err() != io.EOF {
				return fail()
			}
			break
		}
		a := portalTokenAttrs(t)
		if kind == html.StartTagToken {
			if t.Data == "a" {
				if target, err := PythonJoinURL(request, a["href"]); err == nil {
					if canonical, ok := PageUpJobURL(target, board); ok {
						rawURLs[canonical] = true
					}
				}
				if portalHasClass(a["class"], "job-link") {
					jobHref, jobActive = a["href"], false
					if _, exists := a["href"]; exists {
						jobActive = true
					}
					jobText.Reset()
				}
				if portalHasClass(a["class"], "more-link") {
					target, err := PythonJoinURL(request, a["href"])
					identity, ok := pageupPageIdentity(target, board)
					n, e := strconv.Atoi(a["data-page"])
					m, f := strconv.Atoi(a["data-page-items"])
					if err != nil || !ok || e != nil || f != nil || !smallUnsigned.MatchString(a["data-page"]) || !smallUnsigned.MatchString(a["data-page-items"]) || identity != [2]int{n, m} {
						return fail()
					}
					next[identity], more, moreActive = true, identity, true
					moreText.Reset()
				}
			}
			if t.Data == "span" && portalHasClass(a["class"], "result-count") {
				inTotal = true
				totalText.Reset()
			}
		} else if kind == html.TextToken {
			if jobActive {
				jobText.WriteString(t.Data)
			}
			if moreActive {
				moreText.WriteString(t.Data)
				moreText.WriteByte(' ')
			}
			if inTotal {
				totalText.WriteString(t.Data)
			}
		} else if kind == html.EndTagToken {
			if t.Data == "a" && jobActive {
				target, err := PythonJoinURL(request, jobHref)
				canonical, ok := PageUpJobURL(target, board)
				title := inlineNormalized(jobText.String())
				if err == nil && ok && title != "" {
					previous, exists := titles[canonical]
					if !exists || generic(previous) && !generic(title) {
						titles[canonical] = title
					} else if !generic(previous) && !generic(title) && previous != title {
						return fail()
					}
				}
				jobActive = false
			}
			if t.Data == "a" && moreActive {
				values := pageupRemaining.FindAllString(moreText.String(), -1)
				if len(values) == 0 {
					return fail()
				}
				n, err := strconv.Atoi(strings.ReplaceAll(values[len(values)-1], ",", ""))
				if err != nil {
					return fail()
				}
				if remaining[more] == nil {
					remaining[more] = map[int]bool{}
				}
				remaining[more][n] = true
				moreActive = false
			}
			if t.Data == "span" && inTotal {
				raw := strings.ReplaceAll(inlineTrim(totalText.String()), ",", "")
				if smallUnsigned.MatchString(raw) {
					n, err := strconv.Atoi(raw)
					if err != nil {
						return fail()
					}
					totals[n] = true
				}
				inTotal = false
			}
		}
	}
	if len(rawURLs) != len(titles) || len(totals) > 1 || len(assertions) == 0 && len(rawURLs) == 0 {
		return fail()
	}
	for key := range rawURLs {
		if _, exists := titles[key]; !exists {
			return fail()
		}
	}
	offset, total := (page-1)*size, 0
	if len(totals) == 1 {
		for n := range totals {
			total = n
		}
	} else if len(next) == 1 {
		for key := range next {
			if len(remaining[key]) != 1 {
				return fail()
			}
			for n := range remaining[key] {
				total = offset + len(rawURLs) + n
			}
		}
	} else if len(next) == 0 {
		total = offset + len(rawURLs)
	} else {
		return fail()
	}
	if total > 50_000 || expectedTotal != nil && total != *expectedTotal {
		return fail()
	}
	expectedJobs := max(min(size, total-offset), 0)
	hasNext := offset+expectedJobs < total
	if len(rawURLs) != expectedJobs || hasNext && (len(next) != 1 || !next[[2]int{page + 1, size}]) || !hasNext && (len(next) != 0 || len(remaining) != 0) {
		return fail()
	}
	if hasNext {
		values := remaining[[2]int{page + 1, size}]
		if len(values) != 1 || !values[total-offset-expectedJobs] {
			return fail()
		}
	}
	keys := make([]string, 0, len(titles))
	for key := range titles {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	jobs := []map[string]any{}
	for _, key := range keys {
		jobs = append(jobs, map[string]any{"url": key, "title": titles[key]})
	}
	return PageUpPage{Jobs: jobs, Total: total, HasNext: hasNext}, nil
}
