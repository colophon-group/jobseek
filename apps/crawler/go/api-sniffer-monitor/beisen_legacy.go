package apisniffer

import (
	"encoding/json"
	"io"
	"net/url"
	"regexp"
	"strconv"
	"strings"

	"golang.org/x/net/html"
)

var beisenPageIndex = regexp.MustCompile(`(?i)[?&](?:amp;)?PageIndex=([0-9]+)`)
var beisenTotalPages = regexp.MustCompile(`当前第\s*\d+\s*/\s*(\d+)\s*页`)
var beisenCurrentPage = regexp.MustCompile(`(?i)class=['"][^'"]*\b(?:now|current)\b[^'"]*['"][^>]*>\s*(\d+)\s*<`)
var beisenRecordTotal = regexp.MustCompile(`共\s*([\d,]+)\s*条记录`)
var beisenISODate = regexp.MustCompile(`^\d{4}-\d{2}-\d{2}$`)
var beisenHrefID = regexp.MustCompile(`(?i)^/zpdetail/([1-9][0-9]{0,18})/?$`)

type BeisenLegacyPage struct {
	Jobs  []Job
	Pages int
	Total *int
}

func beisenLegacyHref(href, tenant string) string {
	u, err := url.Parse(href)
	if err != nil || u.Scheme != "" && u.Scheme != "https" || u.Hostname() != "" && !strings.EqualFold(u.Hostname(), tenant+".zhiye.com") || u.User != nil || u.Fragment != "" {
		return ""
	}
	query, err := url.ParseQuery(u.RawQuery)
	count := 0
	for key, values := range query {
		for _, value := range values {
			count++
			if !strings.EqualFold(key, "pageindex") || value == "" {
				return ""
			}
			for _, r := range value {
				if r < '0' || r > '9' {
					return ""
				}
			}
		}
	}
	if err != nil || count > 1 {
		return ""
	}
	m := beisenHrefID.FindStringSubmatch(u.Path)
	if len(m) != 2 {
		return ""
	}
	return m[1]
}

// Use tokenizer events rather than repaired DOM rows: Python HTMLParser does
// not implicitly close malformed table/list tags. End-of-document cannot turn
// an unfinished row into an authoritative job.
func ParseBeisenLegacyPage(source string, b BeisenBoard) (BeisenLegacyPage, error) {
	result := BeisenLegacyPage{Jobs: []Job{}, Pages: 1}
	if !BeisenLegacyMarker.MatchString(source) {
		return result, ErrInventory
	}
	rowDepth, cellDepth, liDepth, spanDepth := 0, 0, 0, 0
	cells, spans := []string{}, []string{}
	cellText, spanText := []string{}, []string{}
	jobID, title, inlineID := "", "", ""
	start := func(tag string, attrs map[string]string) {
		if b.LegacyTemplate == "standard" {
			switch {
			case tag == "tr":
				rowDepth++
				if rowDepth == 1 {
					cells = []string{}
					jobID, title = "", ""
				}
			case rowDepth > 0 && tag == "td":
				cellDepth++
				if cellDepth == 1 {
					cellText = []string{}
				}
			case rowDepth > 0 && tag == "a":
				if id := beisenLegacyHref(attrs["href"], b.Tenant); id != "" {
					jobID = id
					title = cleanBeisenString(attrs["title"])
				}
			}
			return
		}
		if tag == "li" {
			liDepth++
			if liDepth == 1 {
				spans = []string{}
				inlineID = ""
			}
		} else if liDepth > 0 && tag == "span" {
			spanDepth++
			if spanDepth == 1 {
				spanText = []string{}
			}
		}
		if liDepth > 0 {
			if id := strings.TrimSpace(attrs["jobadid"]); beisenLegacyID.MatchString(id) {
				inlineID = id
			}
		}
	}
	end := func(tag string) {
		if b.LegacyTemplate == "standard" {
			if tag == "td" && cellDepth > 0 {
				cellDepth--
				if cellDepth == 0 {
					cells = append(cells, cleanBeisenString(strings.Join(cellText, "")))
				}
			} else if tag == "tr" && rowDepth > 0 {
				rowDepth--
				if rowDepth == 0 && jobID != "" {
					job := Job{URL: b.LegacyJobURL(jobID), Metadata: map[string]any{"job_ad_id": json.Number(jobID)}}
					if title != "" {
						job.Title = title
					} else if len(cells) > 0 && cells[0] != "" {
						job.Title = cells[0]
					}
					for i, value := range cells {
						if beisenISODate.MatchString(value) {
							job.DatePosted = safeBeisenDate(value)
							if i > 0 && cells[i-1] != "" {
								job.Locations = []string{cells[i-1]}
							}
							break
						}
					}
					result.Jobs = append(result.Jobs, job)
				}
			}
			return
		}
		if tag == "span" && spanDepth > 0 {
			spanDepth--
			if spanDepth == 0 {
				spans = append(spans, cleanBeisenString(strings.Join(spanText, "")))
			}
		} else if tag == "li" && liDepth > 0 {
			liDepth--
			if liDepth == 0 && inlineID != "" && len(spans) >= 5 {
				job := Job{URL: b.LegacyJobURL(inlineID), DatePosted: safeBeisenDate(spans[4]), Metadata: map[string]any{"job_ad_id": json.Number(inlineID)}}
				if spans[0] != "" {
					job.Title = spans[0]
				}
				if spans[3] != "" {
					job.Locations = []string{spans[3]}
				}
				result.Jobs = append(result.Jobs, job)
			}
		}
	}
	z := html.NewTokenizer(strings.NewReader(source))
	for {
		kind := z.Next()
		if kind == html.ErrorToken {
			if z.Err() != io.EOF {
				return result, ErrInventory
			}
			break
		}
		token := z.Token()
		switch kind {
		case html.StartTagToken, html.SelfClosingTagToken:
			attrs := map[string]string{}
			for _, a := range token.Attr {
				attrs[a.Key] = a.Val
			}
			start(token.Data, attrs)
			if kind == html.SelfClosingTagToken {
				end(token.Data)
			}
		case html.EndTagToken:
			end(token.Data)
		case html.TextToken:
			if cellDepth > 0 {
				cellText = append(cellText, token.Data)
			}
			if spanDepth > 0 {
				spanText = append(spanText, token.Data)
			}
		}
	}
	for _, match := range beisenPageIndex.FindAllStringSubmatchIndex(source, -1) {
		end := match[1]
		if end < len(source) && !strings.ContainsRune("&#\"' \t\r\n\v\f", rune(source[end])) {
			continue
		}
		n, err := strconv.Atoi(source[match[2]:match[3]])
		if err != nil {
			return result, ErrInventory
		}
		if n > result.Pages {
			result.Pages = n
		}
	}
	for _, pattern := range []*regexp.Regexp{beisenTotalPages, beisenCurrentPage} {
		if match := pattern.FindStringSubmatch(source); len(match) == 2 {
			n, err := strconv.Atoi(match[1])
			if err != nil {
				return result, ErrInventory
			}
			if n > result.Pages {
				result.Pages = n
			}
		}
	}
	if match := beisenRecordTotal.FindStringSubmatch(source); len(match) == 2 {
		n, err := strconv.Atoi(strings.ReplaceAll(match[1], ",", ""))
		if err != nil {
			return result, ErrInventory
		}
		result.Total = &n
	}
	return result, nil
}
