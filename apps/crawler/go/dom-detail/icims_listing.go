package dom

import (
	"github.com/andybalholm/cascadia"
	"golang.org/x/net/html"
	stdhtml "html"
	"net/url"
	"regexp"
	"strconv"
	"strings"
	"unicode"
	"unicode/utf8"
)

func strconvICIMS(n int) string { return strconv.Itoa(n) }

var icimsPageMarker = regexp.MustCompile(`(?i)\bPage\s+(\d+)\s+of\s+(\d+)\b`)
var icimsPageParam = regexp.MustCompile(`(?:[?&])pr=(\d+)`)

func ICIMSPageMetadata(source string) (int, int) {
	current, total := 0, 1
	var err error
	if m := icimsPageMarker.FindStringSubmatch(source); m != nil {
		current, err = strconv.Atoi(m[1])
		if err != nil || current == 0 {
			current = -1
		}
		total, err = strconv.Atoi(m[2])
		if err != nil {
			total = 1001
		}
	}
	decoded := stdhtml.UnescapeString(source)
	for _, m := range icimsPageParam.FindAllStringSubmatchIndex(decoded, -1) {
		if m[1] < len(decoded) {
			r, _ := utf8.DecodeRuneInString(decoded[m[1]:])
			if !strings.ContainsRune("&#\"'", r) && !unicode.IsSpace(r) {
				continue
			}
		}
		n, err := strconv.Atoi(decoded[m[2]:m[3]])
		if err != nil {
			return current, 1001
		}
		if n+1 > total {
			total = n + 1
		}
	}
	if total < 1 {
		total = 1
	}
	return current, total
}
func ICIMSListingURLs(source, host string, hosts []string) (map[string]bool, error) {
	raw, err := ListingHrefs(source, "")
	if err != nil {
		return nil, err
	}
	base, _ := url.Parse(ICIMSListingURL(host, 0))
	out := map[string]bool{}
	for _, href := range raw {
		u, err := url.Parse(strings.TrimSpace(stdhtml.UnescapeString(href)))
		if err != nil {
			continue
		}
		canonical := ICIMSCanonicalJobURL(base.ResolveReference(u).String(), hosts)
		if canonical != "" {
			out[canonical] = true
		}
	}
	return out, nil
}

type ICIMSIdentity struct{ Title, Region, JobType string }

func icimsSelect(n *html.Node, selector string) *html.Node {
	return cascadia.Query(n, cascadia.MustCompile(selector))
}
func icimsAttr(n *html.Node, key string) string {
	if n == nil {
		return ""
	}
	for _, a := range n.Attr {
		if a.Key == key {
			return a.Val
		}
	}
	return ""
}
func icimsText(n *html.Node) string {
	var out strings.Builder
	var visit func(*html.Node)
	visit = func(x *html.Node) {
		if x.Type == html.TextNode {
			out.WriteString(x.Data)
		}
		for c := x.FirstChild; c != nil; c = c.NextSibling {
			visit(c)
		}
	}
	if n != nil {
		visit(n)
	}
	return strings.TrimSpace(out.String())
}
func ICIMSListingIdentities(source, host string, hosts []string) (map[string]ICIMSIdentity, error) {
	tree, err := html.Parse(strings.NewReader(source))
	if err != nil {
		return nil, err
	}
	out := map[string]ICIMSIdentity{}
	for _, card := range cascadia.QueryAll(tree, cascadia.MustCompile(".iCIMS_JobCardItem")) {
		anchor, title, region := icimsSelect(card, "a.iCIMS_Anchor"), icimsSelect(card, "h3"), icimsSelect(card, "div.header.left")
		if anchor == nil || title == nil || region == nil {
			return nil, ErrICIMS
		}
		jobURL := ICIMSCanonicalJobURL(icimsAttr(anchor, "href"), hosts)
		rawRegion, jobType := "", ""
		for _, span := range cascadia.QueryAll(region, cascadia.MustCompile("span")) {
			classes := " " + strings.Join(strings.Fields(icimsAttr(span, "class")), " ") + " "
			if !strings.Contains(classes, " sr-only ") {
				rawRegion = icimsText(span)
			}
		}
		for _, field := range cascadia.QueryAll(card, cascadia.MustCompile(".iCIMS_JobHeaderTag")) {
			label, value := icimsSelect(field, "dt"), icimsSelect(field, "dd")
			if label != nil && value != nil && icimsNormalize(icimsText(label)) == "job type" {
				jobType = icimsText(value)
				break
			}
		}
		rawTitle := icimsText(title)
		if jobURL == "" || rawTitle == "" || rawRegion == "" {
			return nil, ErrICIMS
		}
		identity := ICIMSIdentity{icimsNormalize(rawTitle), icimsNormalize(rawRegion), icimsNormalize(jobType)}
		if old, ok := out[jobURL]; ok && old != identity {
			return nil, ErrICIMS
		}
		out[jobURL] = identity
	}
	return out, nil
}
