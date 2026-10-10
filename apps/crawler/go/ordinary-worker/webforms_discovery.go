package worker

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/cookiejar"
	"net/url"
	"regexp"
	"sort"
	"strconv"
	"strings"

	"github.com/andybalholm/cascadia"
	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	"golang.org/x/net/html"
)

const slaughterListing = "https://careers.slaughterandmay.com/VacanciesV2.aspx"

// This is the publisher's public PageSize command for the exact existing
// four-action configuration. Config changes continue through the ordinary
// rendered path; this adapter never turns arbitrary browser actions into POSTs.
func slaughterWebFormsConfigured(profile queue.GreenhouseMonitorProfile, config map[string]string) bool {
	if profile.Provider != "dom" || profile.Profile != "dom.rendered-urls/v1" || profile.Endpoint != slaughterListing || config["board_url"] != slaughterListing {
		return false
	}
	listing, options, err := queue.RenderedDOMMonitorOptions(config)
	if err != nil || listing.Include != "PrintVacancy" || listing.Selector != "" || listing.Pagination != nil || listing.RichRows != nil || listing.RequireJSONLD || listing.IncludeBoardURL || listing.Proofs != nil || listing.ProviderProof != nil || listing.ScriptLinks != nil || listing.OnclickSelector != "" || listing.Encoding != "" || listing.FetchURL != "" || options["request_headers"] != nil || options["resource_policy"] != nil || options["transport_attempts"] != nil {
		return false
	}
	body, err := json.Marshal(options["actions"])
	digest := sha256.Sum256(body)
	return err == nil && hex.EncodeToString(digest[:]) == "2cb547e22d8f454344d4144f44740a37dd9d408ad4fe492133256f208be7c291"
}

type slaughterWebFormsScope struct{}

func (slaughterWebFormsScope) ResourceMatches(raw string) bool { return raw == slaughterListing }

var webFormsGrid = regexp.MustCompile(`"_gridTableViewsData":("(?:[^"\\]|\\.)*")`)
var webFormsVacancy = regexp.MustCompile(`VacancyId=(\d+)&LocationId=(\d+)`)

type webFormsGridState struct {
	UniqueID                                                string
	PageSize, PageCount, CurrentPageIndex, VirtualItemCount int
}

func slaughterGridState(source string) (webFormsGridState, error) {
	fail := errors.New("incomplete WebForms grid")
	matches := webFormsGrid.FindAllStringSubmatch(source, -1)
	if len(matches) != 1 {
		return webFormsGridState{}, fail
	}
	var encoded string
	var grids []webFormsGridState
	if json.Unmarshal([]byte(matches[0][1]), &encoded) != nil || json.Unmarshal([]byte(encoded), &grids) != nil || len(grids) != 1 {
		return webFormsGridState{}, fail
	}
	g := grids[0]
	if g.UniqueID != "ctl00$Cph1$vcyS$vsGrid$ctl00" || g.CurrentPageIndex != 0 || g.VirtualItemCount < 1 || g.VirtualItemCount > 50 || g.PageSize != 10 && g.PageSize != 50 || g.PageCount != (g.VirtualItemCount+g.PageSize-1)/g.PageSize {
		return g, fail
	}
	return g, nil
}

func slaughterPostBody(source string) (string, error) {
	tree, err := html.Parse(strings.NewReader(source))
	if err != nil {
		return "", err
	}
	forms := cascadia.QueryAll(tree, cascadia.MustCompile("form#form2"))
	if len(forms) != 1 || nodeAttribute(forms[0], "method") != "post" || nodeAttribute(forms[0], "action") != "./VacanciesV2.aspx" {
		return "", errors.New("unsupported WebForms form")
	}
	fields := []string{}
	found := map[string]int{}
	for _, node := range cascadia.QueryAll(forms[0], cascadia.MustCompile("input[name]")) {
		kind := strings.ToLower(nodeAttribute(node, "type"))
		if kind == "submit" || kind == "button" || kind == "image" || kind == "reset" || (kind == "checkbox" || kind == "radio") && !hasNodeAttribute(node, "checked") || hasNodeAttribute(node, "disabled") {
			continue
		}
		name, value := nodeAttribute(node, "name"), nodeAttribute(node, "value")
		if len(name) > 512 || len(value) > 1<<20 || len(fields) > 1024 {
			return "", errors.New("WebForms field limit")
		}
		found[name]++
		switch name {
		case "__EVENTTARGET":
			value = "ctl00$Cph1$vcyS$vsGrid"
		case "__EVENTARGUMENT":
			value = "FireCommand:ctl00$Cph1$vcyS$vsGrid$ctl00;PageSize;50"
		case "__VIEWSTATE_UNIQUE_KEY":
			if value == "" {
				return "", errors.New("empty WebForms session field")
			}
		}
		fields = append(fields, url.QueryEscape(name)+"="+url.QueryEscape(value))
	}
	for _, name := range []string{"__VIEWSTATE", "__VIEWSTATE_UNIQUE_KEY"} {
		if found[name] != 1 {
			return "", errors.New("ambiguous WebForms session fields")
		}
	}
	for _, event := range []struct{ name, value string }{
		{"__EVENTTARGET", "ctl00$Cph1$vcyS$vsGrid"},
		{"__EVENTARGUMENT", "FireCommand:ctl00$Cph1$vcyS$vsGrid$ctl00;PageSize;50"},
	} {
		if found[event.name] > 1 {
			return "", errors.New("ambiguous WebForms event")
		}
		if found[event.name] == 0 {
			fields = append(fields, url.QueryEscape(event.name)+"="+url.QueryEscape(event.value))
		}
	}
	return strings.Join(fields, "&"), nil
}

func nodeAttribute(node *html.Node, key string) string {
	for _, a := range node.Attr {
		if a.Key == key {
			return a.Val
		}
	}
	return ""
}

func hasNodeAttribute(node *html.Node, key string) bool {
	for _, a := range node.Attr {
		if a.Key == key {
			return true
		}
	}
	return false
}

func slaughterInventory(source string) ([]RichMonitorJob, error) {
	g, err := slaughterGridState(source)
	if err != nil || g.PageSize != 50 || g.PageCount != 1 {
		return nil, errors.New("WebForms pagination incomplete")
	}
	tree, err := html.Parse(strings.NewReader(source))
	if err != nil {
		return nil, err
	}
	urls := map[string]bool{}
	for _, node := range cascadia.QueryAll(tree, cascadia.MustCompile(`input[onclick*="PrintVacancy"]`)) {
		matches := webFormsVacancy.FindAllStringSubmatch(nodeAttribute(node, "onclick"), -1)
		if len(matches) != 1 {
			return nil, errors.New("malformed WebForms vacancy control")
		}
		for _, n := range matches[0][1:] {
			if value, err := strconv.ParseUint(n, 10, 64); err != nil || value == 0 {
				return nil, errors.New("invalid WebForms vacancy identity")
			}
		}
		urls["https://careers.slaughterandmay.com/PrintVacancy.aspx?mode=View&VacancyId="+matches[0][1]+"&LocationId="+matches[0][2]] = true
	}
	if len(urls) != g.VirtualItemCount {
		return nil, errors.New("WebForms inventory count mismatch")
	}
	ordered := make([]string, 0, len(urls))
	for raw := range urls {
		ordered = append(ordered, raw)
	}
	sort.Strings(ordered)
	jobs := make([]RichMonitorJob, 0, len(ordered))
	for _, raw := range ordered {
		jobs = append(jobs, RichMonitorJob{URL: raw, URLOnly: true})
	}
	return jobs, nil
}

func discoverSlaughterWebForms(ctx context.Context, verified *http.Client) (RichDiscovery, error) {
	result := RichDiscovery{Jobs: []RichMonitorJob{}}
	if verified == nil {
		return result, queue.ErrConfiguration
	}
	client := *verified
	var err error
	client.Jar, err = cookiejar.New(nil)
	if err != nil {
		return result, err
	}
	client.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	fetch := func(request api.Request) ([]byte, error) {
		body, _, response, err := fetchLastHTTPOnce(ctx, &client, slaughterWebFormsScope{}, request, 2<<20)
		result.Response = response
		return body, err
	}
	body, err := fetch(api.Request{Method: "GET", URL: slaughterListing})
	if err != nil {
		return result, err
	}
	before, err := slaughterGridState(string(body))
	if err != nil {
		return result, err
	}
	post, err := slaughterPostBody(string(body))
	if err != nil {
		return result, err
	}
	body, err = fetch(api.Request{Method: "POST", URL: slaughterListing, Body: post, Headers: http.Header{"Content-Type": {"application/x-www-form-urlencoded"}}})
	if err != nil {
		return result, err
	}
	after, err := slaughterGridState(string(body))
	if err != nil || before.VirtualItemCount != after.VirtualItemCount {
		return result, errors.New("WebForms listing changed during postback")
	}
	result.Jobs, err = slaughterInventory(string(body))
	return result, err
}
