package worker

import (
	"context"
	"errors"
	"net/http"
	"net/http/cookiejar"
	"net/url"
	"reflect"
	"regexp"
	"sort"
	"strconv"
	"strings"

	"github.com/andybalholm/cascadia"
	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	"golang.org/x/net/html"
)

var candidatusCardID = regexp.MustCompile(`^c-(\d+)-A20$`)
var candidatusCardValue = regexp.MustCompile(`\b_PAGE_\.A18\.value=(\d+)\b`)

func candidatusCards(source string, maxJobs int) ([]int, *html.Node, error) {
	fail := errors.New("incomplete Candidatus listing")
	if !strings.Contains(source, "RECRUTEMENT_LISTEANNONCES_XMOD10") || !strings.Contains(source, "_JSL(_PAGE_") {
		return nil, nil, fail
	}
	tree, e := html.Parse(strings.NewReader(source))
	if e != nil {
		return nil, nil, e
	}
	indexes := []int{}
	seen := map[int]bool{}
	for _, node := range cascadia.QueryAll(tree, cascadia.MustCompile(`a[id$="-A20"]`)) {
		id := candidatusCardID.FindStringSubmatch(nodeAttribute(node, "id"))
		value := candidatusCardValue.FindStringSubmatch(nodeAttribute(node, "href"))
		if node.Namespace != "" || len(id) != 2 || len(value) != 2 || id[1] != value[1] {
			return nil, nil, fail
		}
		n, e := strconv.Atoi(id[1])
		if e != nil || strconv.Itoa(n) != id[1] || seen[n] {
			return nil, nil, fail
		}
		seen[n] = true
		indexes = append(indexes, n)
	}
	if len(indexes) == 0 || len(indexes) > maxJobs {
		return nil, nil, fail
	}
	return indexes, tree, nil
}

func candidatusPostback(tree *html.Node, c queue.CandidatusConfig, index, count int) (api.Request, error) {
	fail := func() (api.Request, error) { return api.Request{}, errors.New("unsupported Candidatus form") }
	forms := cascadia.QueryAll(tree, cascadia.MustCompile("form"))
	if len(forms) != 1 || nodeAttribute(forms[0], "name") != "RECRUTEMENT_LISTEANNONCES_XMOD10" || nodeAttribute(forms[0], "method") != "post" {
		return fail()
	}
	base, e := url.Parse(c.ListingURL)
	if e != nil {
		return fail()
	}
	action, e := url.Parse(nodeAttribute(forms[0], "action"))
	if e != nil {
		return fail()
	}
	target := base.ResolveReference(action).String()
	if !c.ResourceMatches(target) || target == c.ListingURL {
		return fail()
	}
	fields := []string{}
	seen := map[string]int{}
	for _, node := range cascadia.QueryAll(forms[0], cascadia.MustCompile("input[name],select[name]")) {
		kind := strings.ToLower(nodeAttribute(node, "type"))
		if hasNodeAttribute(node, "disabled") || kind == "submit" || kind == "button" || kind == "image" || kind == "reset" || (kind == "checkbox" || kind == "radio") && !hasNodeAttribute(node, "checked") {
			continue
		}
		name := nodeAttribute(node, "name")
		values := []string{nodeAttribute(node, "value")}
		if node.Data == "select" {
			options := cascadia.QueryAll(node, cascadia.MustCompile("option[selected]"))
			if len(options) == 0 {
				options = cascadia.QueryAll(node, cascadia.MustCompile("option"))
				if len(options) > 1 {
					options = options[:1]
				}
			}
			values = nil
			for _, option := range options {
				if !hasNodeAttribute(option, "value") {
					return fail()
				}
				values = append(values, nodeAttribute(option, "value"))
			}
		}
		seen[name] += len(values)
		switch name {
		case "WD_BUTTON_CLICK_":
			values = []string{"A20"}
		case "A18":
			values = []string{strconv.Itoa(index)}
		case "_A18_OCC":
			if len(values) != 1 || values[0] != strconv.Itoa(count) {
				return fail()
			}
		}
		for _, value := range values {
			fields = append(fields, url.QueryEscape(name)+"="+url.QueryEscape(value))
		}
	}
	for _, key := range []string{"WD_JSON_PROPRIETE_", "WD_BUTTON_CLICK_", "WD_ACTION_", "A18", "A18_DEB", "_A18_OCC"} {
		if seen[key] != 1 {
			return fail()
		}
	}
	body := strings.Join(fields, "&")
	if len(body) > 4096 {
		return fail()
	}
	return api.Request{Method: "POST", URL: target, Body: body, Headers: http.Header{"Content-Type": {"application/x-www-form-urlencoded"}}}, nil
}

func discoverCandidatusHTTP(ctx context.Context, verified *http.Client, config map[string]string) (RichDiscovery, error) {
	result := RichDiscovery{Jobs: []RichMonitorJob{}}
	c, e := queue.CandidatusMonitorOptions(config)
	if e != nil || verified == nil {
		return result, queue.ErrConfiguration
	}
	client := *verified
	client.Jar, e = cookiejar.New(nil)
	if e != nil {
		return result, e
	}
	client.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	fetch := func(request api.Request) ([]byte, http.Header, error) {
		body, headers, response, e := fetchLastHTTPOnce(ctx, &client, c, request, 2<<20)
		result.Response = response
		return body, headers, e
	}
	var indexes []int
	urls := map[string]bool{}
	for position := 0; position == 0 || position < len(indexes); position++ {
		body, _, e := fetch(api.Request{Method: "GET", URL: c.ListingURL})
		if e != nil {
			return result, e
		}
		current, tree, e := candidatusCards(string(body), c.MaxJobs)
		if e != nil {
			return result, e
		}
		if position == 0 {
			indexes = current
		} else if !reflect.DeepEqual(current, indexes) {
			return result, errors.New("Candidatus listing changed during postbacks")
		}
		request, e := candidatusPostback(tree, c, indexes[position], len(indexes))
		if e != nil {
			return result, e
		}
		_, headers, e := fetch(request)
		var status *DiscoveryError
		if !errors.As(e, &status) || status.Kind != "http_status" || status.Status != 302 || result.Response == nil || result.Response.reserved {
			if e == nil {
				e = errors.New("Candidatus postback did not redirect")
			}
			return result, e
		}
		base, _ := url.Parse(request.URL)
		target, parseErr := url.Parse(headers.Get("Location"))
		if parseErr != nil {
			return result, errors.New("invalid Candidatus redirect")
		}
		canonical, e := queue.CandidatusCanonicalDetailURL(base.ResolveReference(target).String())
		if e != nil || urls[canonical] {
			return result, errors.New("invalid or duplicate Candidatus detail identity")
		}
		urls[canonical] = true
	}
	ordered := make([]string, 0, len(urls))
	for raw := range urls {
		ordered = append(ordered, raw)
	}
	sort.Strings(ordered)
	for _, raw := range ordered {
		result.Jobs = append(result.Jobs, RichMonitorJob{URL: raw, URLOnly: true})
	}
	return result, nil
}
