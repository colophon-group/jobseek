package worker

import (
	"context"
	"encoding/xml"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/cookiejar"
	"net/url"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/andybalholm/cascadia"
	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
	"golang.org/x/net/html"
)

var errOracleADF = errors.New("incomplete Oracle ADF inventory")
var adfPageID = regexp.MustCompile(`\.setPageId\((?:'([A-Za-z0-9_-]{1,128})'|"([A-Za-z0-9_-]{1,128})")\)`)
var adfSeed = regexp.MustCompile(`[?&]jobId=([1-9][0-9]{0,15})$`)

func oracleADFResourceMatches(endpoint, resource string) bool {
	if endpoint == resource {
		return true
	}
	base, e1 := url.Parse(endpoint)
	u, e2 := url.Parse(resource)
	if e1 != nil || e2 != nil || u.Scheme != "https" || u.Host != base.Host || u.Path != base.Path || u.RawPath != "" || u.User != nil || u.Fragment != "" || u.Opaque != "" {
		return false
	}
	q, e := url.ParseQuery(u.RawQuery)
	if e != nil {
		return false
	}
	if len(q) == 1 && len(q["jobId"]) == 1 {
		return adfSeed.MatchString("?jobId=" + q.Get("jobId"))
	}
	return len(q) == 2 && len(q["Adf-Page-Id"]) == 1 && len(q["Adf-Window-Id"]) == 1 && len(q.Get("Adf-Page-Id")) > 0 && len(q.Get("Adf-Page-Id")) <= 128 && len(q.Get("Adf-Window-Id")) > 0 && len(q.Get("Adf-Window-Id")) <= 256
}

type adfRow struct{ title, location string }

func adfText(node *html.Node) string {
	var b strings.Builder
	var walk func(*html.Node)
	walk = func(n *html.Node) {
		if n.Type == html.TextNode {
			b.WriteString(n.Data)
			b.WriteByte(' ')
		}
		for c := n.FirstChild; c != nil; c = c.NextSibling {
			walk(c)
		}
	}
	walk(node)
	return strings.Join(strings.Fields(b.String()), " ")
}

func adfOneText(tree *html.Node, selector string) (string, error) {
	nodes := cascadia.QueryAll(tree, cascadia.MustCompile(selector))
	if len(nodes) != 1 {
		return "", errOracleADF
	}
	value := adfText(nodes[0])
	if value == "" {
		return "", errOracleADF
	}
	return value, nil
}

// More is part of each appended ADF block. Its absence, rather than a short
// batch, proves completion, including inventories that are exact page multiples.
func adfRows(source string, offset int) ([]adfRow, bool, error) {
	tree, err := html.Parse(strings.NewReader(source))
	if err != nil {
		return nil, false, errOracleADF
	}
	nodes := cascadia.QueryAll(tree, cascadia.MustCompile(`[role="gridcell"][data-afrRK]`))
	rows := []adfRow{}
	for i, n := range nodes {
		index := strconv.Itoa(offset + i)
		if nodeAttribute(n, "data-afrrk") != index || nodeAttribute(n, "id") != "pt1:r1:0:lv1:"+index+":li1" {
			return nil, false, errOracleADF
		}
		title, e1 := adfOneText(n, `[id$="ol1"]`)
		location, e2 := adfOneText(n, `[id$="of4"]`)
		if e1 != nil || e2 != nil {
			return nil, false, errOracleADF
		}
		rows = append(rows, adfRow{title, location})
	}
	more := cascadia.QueryAll(tree, cascadia.MustCompile(`a[id*="::fchmrlnk"]`))
	if len(rows) == 0 || len(more) > 1 || len(more) == 1 && nodeAttribute(more[0], "data-afrfchindx") != strconv.Itoa(offset+len(rows)-1) {
		return nil, false, errOracleADF
	}
	return rows, len(more) == 1, nil
}

func adfSession(source string) (url.Values, string, error) {
	tree, err := html.Parse(strings.NewReader(source))
	if err != nil {
		return nil, "", errOracleADF
	}
	forms := cascadia.QueryAll(tree, cascadia.MustCompile(`form#f1`))
	matches := adfPageID.FindAllStringSubmatch(source, -1)
	if len(forms) != 1 || len(matches) != 1 {
		return nil, "", errOracleADF
	}
	fields := url.Values{}
	for _, n := range cascadia.QueryAll(forms[0], cascadia.MustCompile(`input[type="hidden"][name]`)) {
		name, value := nodeAttribute(n, "name"), nodeAttribute(n, "value")
		if len(name) > 256 || len(value) > 1<<20 || len(fields) >= 32 || len(fields[name]) != 0 {
			return nil, "", errOracleADF
		}
		fields.Set(name, value)
	}
	if len(fields) != 3 || fields.Get("org.apache.myfaces.trinidad.faces.FORM") != "f1" || fields.Get("Adf-Window-Id") == "" || fields.Get("javax.faces.ViewState") == "" {
		return nil, "", errOracleADF
	}
	// This reads only a bounded literal token; no publisher JavaScript executes.
	return fields, matches[0][1] + matches[0][2], nil
}

func adfUpdates(source string) (map[string]string, error) {
	decoder := xml.NewDecoder(strings.NewReader(source))
	updates := map[string]string{}
	depth := 0
	seenRoot := false
	for {
		token, err := decoder.Token()
		if err == io.EOF {
			break
		}
		if err != nil {
			return nil, errOracleADF
		}
		switch t := token.(type) {
		case xml.StartElement:
			if depth == 0 && (t.Name.Local != "partial-response" || seenRoot) || t.Name.Local == "error" || t.Name.Local == "redirect" {
				return nil, errOracleADF
			}
			if depth == 0 {
				seenRoot = true
			}
			if t.Name.Local == "update" {
				id := ""
				for _, a := range t.Attr {
					if a.Name.Local == "id" {
						id = a.Value
					}
				}
				if id == "" || len(updates) >= 8 {
					return nil, errOracleADF
				}
				if _, duplicate := updates[id]; duplicate {
					return nil, errOracleADF
				}
				var value string
				if decoder.DecodeElement(&value, &t) != nil {
					return nil, errOracleADF
				}
				updates[id] = value
			} else {
				depth++
			}
		case xml.EndElement:
			depth--
		}
	}
	if depth != 0 || updates["javax.faces.ViewState"] == "" {
		return nil, errOracleADF
	}
	return updates, nil
}

func adfPost(fields url.Values, pageID string, event, index int, view bool) url.Values {
	form := url.Values{}
	for k, v := range fields {
		form[k] = append([]string{}, v...)
	}
	form.Set("Adf-Page-Id", pageID)
	list := "pt1:r1:0:lv1"
	form.Set("event", list)
	form.Set("oracle.adf.view.rich.PROCESS", list)
	form.Set("event."+list, fmt.Sprintf(`<m xmlns="http://oracle.com/richClient/comm"><k v="id"><n>%d</n></k><k v="subtype"><n>0</n></k><k v="clientKey"><s>%d</s></k><k v="index"><n>%d</n></k><k v="suppressMessageClear"><s>true</s></k><k v="type"><s>fetch</s></k></m>`, event, index, index))
	if view {
		action := list + ":0:l1_view"
		form.Set("event", list+","+action)
		form.Set("event."+list, `<m xmlns="http://oracle.com/richClient/comm"><k v="type"><s>selection</s></k></m>`)
		form.Set("event."+action, `<m xmlns="http://oracle.com/richClient/comm"><k v="type"><s>action</s></k></m>`)
		form.Set("oracle.adf.view.rich.PROCESS", list+",pt1:r1,"+action)
		form.Set("oracle.adf.view.rich.RENDER", "pt1:r1")
		form.Set("oracle.adf.view.rich.DELTAS", "{pt1:r1:0:lv1={selectedRowKeys=0}}")
	}
	return form
}

func discoverOracleADF(ctx context.Context, verified *http.Client, p queue.GreenhouseMonitorProfile, config map[string]string, options queue.OracleADFOptions, wait func(context.Context, time.Duration) error) (RichDiscovery, error) {
	out := RichDiscovery{}
	current, err := queue.InspectRichMonitor(p.BoardID, config)
	actual, optionErr := queue.OracleADFMonitorOptions(config)
	if err != nil || optionErr != nil || actual != options || current.Profile != p.Profile || current.Endpoint != p.Endpoint || current.EffectiveConfigSHA256 != p.EffectiveConfigSHA256 || verified == nil || wait == nil {
		return out, queue.ErrConfiguration
	}
	client := *verified
	client.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	client.Jar, err = cookiejar.New(nil)
	if err != nil {
		return out, err
	}
	observation := &lastHTTPObservation{}
	stage := "bootstrap"
	fetchFailure := func(err error) (string, error) {
		if err == nil {
			err = errOracleADF
		}
		return "", err
	}
	fetch := func(call context.Context, r api.Request, stateless bool) (string, error) {
		next := client
		if stateless {
			next.Jar = nil
		}
		r.Headers = r.Headers.Clone()
		if r.Headers == nil {
			r.Headers = http.Header{}
		}
		r.Headers.Set("User-Agent", "jobseek-crawler/1.0")
		for attempt := 0; attempt < 3; attempt++ {
			attemptCtx, cancel := context.WithTimeout(call, time.Duration(options.TimeoutMS)*time.Millisecond)
			body, _, response, e := fetchLastHTTPOnce(attemptCtx, &next, exactListingResource(r.URL), r, 10_000_000)
			cancel()
			if response != nil {
				response.domVerification = &domVerificationBinding{p.Endpoint, p.BoardID, p.EffectiveConfigSHA256, r.URL, domVerificationConfig(config)}
			}
			observation.observe(response)
			if response != nil && response.reserved {
				return "", e
			}
			if e == nil && len(body) > 0 {
				text := string(body)
				classified, e := dom.ClassifyDocument(text, dom.Object{}, r.URL)
				if e != nil || classified["classification"] == "challenge" {
					return "", &DiscoveryError{Kind: "challenge", cause: errOracleADF}
				}
				return text, nil
			}
			// Mutating form actions are never retried in the same session.
			if r.Method == "POST" {
				return fetchFailure(e)
			}
			if response != nil && (response.status == 404 || response.status == 410) && stateless {
				return "", nil
			}
			var de *DiscoveryError
			if call.Err() != nil || errors.As(e, &de) && de.Kind == "body_limit" {
				return fetchFailure(e)
			}
			status := 0
			if response != nil {
				status = response.status
			}
			if attempt == 2 || status != 0 && status != 200 && status != 401 && status != 403 && status != 408 && status != 425 && status != 429 && status < 500 {
				return fetchFailure(e)
			}
			if e := wait(ctx, time.Duration(500*(1<<attempt))*time.Millisecond); e != nil {
				return "", e
			}
		}
		return "", errOracleADF
	}
	finish := func(e error) (RichDiscovery, error) {
		if e != nil {
			kind, status := "oracle_adf_"+stage, 0
			var failure *DiscoveryError
			if errors.As(e, &failure) {
				status = failure.Status
				switch failure.Kind {
				case "http_status", "request_failed", "body_failed", "body_limit", "challenge":
					kind += "_" + failure.Kind
				}
			}
			e = &DiscoveryError{Kind: kind, Status: status, cause: e}
		}
		return RichDiscovery{Response: observation.latest()}, e
	}
	source, err := fetch(ctx, api.Request{Method: "GET", URL: p.Endpoint}, false)
	if err != nil {
		return finish(err)
	}
	stage = "session"
	fields, page, err := adfSession(source)
	if err != nil {
		return finish(err)
	}
	stage = "listing"
	rows, more, err := adfRows(source, 0)
	if err != nil || len(rows) > options.MaxItems {
		return finish(errOracleADF)
	}
	base, _ := url.Parse(p.Endpoint)
	base.RawQuery, base.ForceQuery, base.Fragment = "", false, ""
	post := *base
	post.RawQuery = url.Values{"Adf-Window-Id": {fields.Get("Adf-Window-Id")}, "Adf-Page-Id": {page}}.Encode()
	postAction := func(event int, view bool) (map[string]string, error) {
		form := adfPost(fields, page, event, len(rows)-1, view)
		body, e := fetch(ctx, api.Request{Method: "POST", URL: post.String(), Body: form.Encode(), Headers: http.Header{"Content-Type": {"application/x-www-form-urlencoded; charset=UTF-8"}, "Referer": {p.Endpoint}, "Adf-Rich-Message": {"true"}, "Adf-Ads-Page-Id": {page}}}, false)
		if e != nil {
			return nil, e
		}
		updates, e := adfUpdates(body)
		if e != nil {
			return nil, e
		}
		// PPR may HTML-escape or CDATA-wrap fragment metadata. Inspect each
		// decoded update too; XML framing cannot hide a publisher reservation.
		for _, fragment := range updates {
			if e := policy.Check(nil, fragment, post.String()); e != nil {
				var reservation *policy.Reservation
				if errors.As(e, &reservation) {
					r := observation.latest()
					r.reserved, r.policy, r.reservationSource = true, reservation.PolicyURL, reservation.Source
				}
				return nil, e
			}
		}
		fields.Set("javax.faces.ViewState", updates["javax.faces.ViewState"])
		return updates, nil
	}
	stage = "pagination"
	for event := 0; more; event++ {
		if event >= options.MaxItems || len(rows) >= options.MaxItems {
			return finish(errOracleADF)
		}
		updates, e := postAction(event, false)
		if e != nil {
			return finish(e)
		}
		next, nextMore, e := adfRows(updates["pt1:r1:0:lv1"], len(rows))
		if e != nil || len(rows)+len(next) > options.MaxItems {
			return finish(errOracleADF)
		}
		rows, more = append(rows, next...), nextMore
	}
	stage = "seed"
	updates, err := postAction(0, true)
	if err != nil {
		return finish(err)
	}
	tree, err := html.Parse(strings.NewReader(updates["pt1:r1"]))
	if err != nil {
		return finish(errOracleADF)
	}
	links := cascadia.QueryAll(tree, cascadia.MustCompile(`a[href*="?jobId="]`))
	if len(links) != 1 {
		return finish(errOracleADF)
	}
	match := adfSeed.FindStringSubmatch(nodeAttribute(links[0], "href"))
	if len(match) != 2 {
		return finish(errOracleADF)
	}
	seed, err := strconv.ParseInt(match[1], 10, 64)
	if err != nil {
		return finish(errOracleADF)
	}
	stage = "detail_scan"
	matched := 0
	urls := []string{}
	floor := max(int64(0), seed-int64(options.MaxScan))
	for start := seed; start > floor && matched < len(rows); start -= 32 {
		n := int(min(int64(32), start-floor))
		bodies := make([]string, n)
		failures := make([]error, n)
		var wg sync.WaitGroup
		tasks := make(chan int)
		for worker := 0; worker < min(8, n); worker++ {
			wg.Go(func() {
				for i := range tasks {
					bodies[i], failures[i] = fetch(ctx, api.Request{Method: "GET", URL: base.String() + "?jobId=" + strconv.FormatInt(start-int64(i), 10)}, true)
				}
			})
		}
		for i := 0; i < n; i++ {
			tasks <- i
		}
		close(tasks)
		wg.Wait()
		for _, e := range failures {
			if e != nil {
				return finish(e)
			}
		}
		for i, body := range bodies {
			if body == "" {
				continue
			}
			detail, e := html.Parse(strings.NewReader(body))
			if e != nil {
				return finish(errOracleADF)
			}
			id, e := adfOneText(detail, `[id$="ot3"]`)
			candidate := strconv.FormatInt(start-int64(i), 10)
			if e != nil || strings.Fields(id)[0] != candidate {
				continue
			}
			title, e1 := adfOneText(detail, `[id$="ot1"] label`)
			location, e2 := adfOneText(detail, `[id$="ot5"]`)
			if e1 != nil || e2 != nil {
				return finish(errOracleADF)
			}
			if (adfRow{title, location}) != rows[matched] {
				continue
			}
			urls = append(urls, base.String()+"?jobId="+candidate)
			matched++
			if matched == len(rows) {
				break
			}
		}
	}
	if matched != len(rows) {
		return finish(errOracleADF)
	}
	sort.Strings(urls)
	for _, source := range urls {
		out.Jobs = append(out.Jobs, RichMonitorJob{URL: source, URLOnly: true})
	}
	out.Response = observation.latest()
	return out, nil
}
