package worker

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	b0task "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/b0task"
	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
	executor "github.com/colophon-group/jobseek/apps/crawler/go/lightpanda-b0-executor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
	"io"
	"net/http"
	"net/url"
	"regexp"
	"strings"
	"unicode/utf8"
)

// Qualified public Salesforce rolePage reader. The original rendered fingerprint,
// parser, enrichment selection and browser queue authority remain unchanged.
// Bootstrap and component data determine the current anonymous read context and
// disclosure locations; downloaded JavaScript is never executed.
type auraHTTPDetailRoute struct{ host, path string }

var qualifiedAuraHTTPDetails = map[string]auraHTTPDetailRoute{
	"6b9facdebd9f562e9aaf6a9f2848bc8e17b8cb0df96ba890092e4966b2ea6a0b": {host: "bambusdev.my.site.com", path: "/s/details"},
}

func qualifiedAuraHTTPDetail(p queue.WorkdayDetailProfile) bool {
	route, ok := qualifiedAuraHTTPDetails[p.EffectiveBoardSHA256]
	if !ok || p.Profile != "dom.rendered-detail/v1" || p.SkipSSL {
		return false
	}
	u, err := url.Parse(p.SourceURL)
	if err != nil || u.Scheme != "https" || u.Opaque != "" || u.User != nil || u.Fragment != "" || u.Hostname() != route.host || (u.Port() != "" && u.Port() != "443") || u.EscapedPath() != route.path {
		return false
	}
	query, err := url.ParseQuery(u.RawQuery)
	return err == nil && len(query["jobReq"]) == 1 && query["jobReq"][0] != "" && len(query["jobReq"][0]) <= 1024 && !strings.ContainsAny(query["jobReq"][0], "\x00\r\n")
}
func fetchQualifiedAuraHTTP(ctx context.Context, verified *VerifiedDirectHTTP, p queue.WorkdayDetailProfile) (map[string]any, *policy.Reservation, error) {
	if !qualifiedAuraHTTPDetail(p) || verified == nil || verified.client == nil || verified.proxyRequired || verified.skipSSL {
		return nil, nil, queue.ErrConfiguration
	}
	document, reserved, err := fetchAuraDocument(ctx, verified, p.SourceURL)
	if reserved != nil {
		return nil, reserved, nil
	}
	if err != nil {
		return nil, nil, err
	}
	raw, err := json.Marshal(p.DOMConfig)
	if err != nil {
		return nil, nil, err
	}
	value, err := b0task.ParseCanonicalValue(raw)
	if err != nil {
		return nil, nil, err
	}
	options, ok := value.(map[string]any)
	if !ok {
		return nil, nil, queue.ErrConfiguration
	}
	fields, err := dom.Parse(document, dom.Object(options), &p.SourceURL)
	return fields, nil, err
}

var publicAuraConfigStart = regexp.MustCompile(`\bauraConfig\s*=\s*\{`)
var publicAuraLabel = regexp.MustCompile(`"DiscloseCompDetailsList"\s*:\s*("(?:[^"\\]|\\.)*")`)

func fetchAuraDocument(ctx context.Context, verified *VerifiedDirectHTTP, source string) (string, *policy.Reservation, error) {
	fail := errors.New("public Aura response contract failed")
	if verified == nil || verified.client == nil || verified.proxyRequired || verified.skipSSL {
		return "", nil, fail
	}
	base, e := url.Parse(source)
	if e != nil || base.Scheme != "https" || base.User != nil || base.Fragment != "" {
		return "", nil, fail
	}
	client := *verified.client
	client.Jar = nil
	client.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	redirectTarget := ""
	read := func(method, target, body string, htmlBody bool) ([]byte, error) {
		if len(target) > 8192 || len(body) > 65536 || (method != http.MethodGet && method != http.MethodPost) {
			return nil, fail
		}
		u, e := url.Parse(target)
		if e != nil || u.Scheme != base.Scheme || u.Host != base.Host || u.User != nil || u.Fragment != "" {
			return nil, fail
		}
		req, e := http.NewRequestWithContext(ctx, method, target, strings.NewReader(body))
		if e != nil {
			return nil, fail
		}
		if method == "POST" {
			req.Header.Set("Content-Type", "application/x-www-form-urlencoded; charset=UTF-8")
			req.Header.Set("Origin", base.Scheme+"://"+base.Host)
			req.Header.Set("Referer", source)
		}
		redirectTarget = ""
		resp, e := client.Do(req)
		if e != nil {
			if ctx.Err() != nil {
				return nil, ctx.Err()
			}
			return nil, fail
		}
		defer resp.Body.Close()
		signals := &runtimev1.ResourcePolicySignals{}
		if value := resp.Header.Get("TDM-Reservation"); value != "" {
			signals.TdmReservationHeader = &value
		}
		if value := resp.Header.Get("TDM-Policy"); value != "" {
			signals.TdmPolicyHeader = &value
		}
		// Script/JSON resources have header signals. HTML metadata retains its
		// existing precedence over headers; neither errors nor redirects skip policy.
		if !htmlBody {
			if err := policy.Check(signals, "", target); err != nil {
				return nil, err
			}
		}
		bodyBytes, err := io.ReadAll(io.LimitReader(resp.Body, 4_000_001))
		if err != nil || len(bodyBytes) > 4_000_000 || !utf8.Valid(bodyBytes) {
			if ctx.Err() != nil {
				return nil, ctx.Err()
			}
			return nil, fail
		}
		if htmlBody {
			if err := policy.Check(signals, string(bodyBytes), target); err != nil {
				return nil, err
			}
		}
		if resp.StatusCode != http.StatusOK {
			if method == http.MethodGet && !htmlBody && (resp.StatusCode == 301 || resp.StatusCode == 302 || resp.StatusCode == 303 || resp.StatusCode == 307 || resp.StatusCode == 308) {
				redirectTarget = resp.Header.Get("Location")
			}
			if htmlBody {
				return nil, &executor.NavigationHTTPError{RequestedURL: source, ResponseURL: target, Status: uint32(resp.StatusCode)}
			}
			return nil, fail
		}
		return bodyBytes, nil
	}
	initial, e := read("GET", source, "", true)
	if e != nil {
		var p *policy.Reservation
		errors.As(e, &p)
		return "", p, e
	}
	var cfg map[string]json.RawMessage
	matches := publicAuraConfigStart.FindAllIndex(initial, -1)
	valid := 0
	for _, m := range matches {
		start := m[1] - 1
		dec := json.NewDecoder(bytes.NewReader(initial[start:]))
		var candidate map[string]json.RawMessage
		if dec.Decode(&candidate) != nil || candidate["context"] == nil || candidate["auraCmpDefBaseURI"] == nil {
			continue
		}
		cfg = candidate
		valid++
	}
	if valid != 1 {
		return "", nil, fail
	}
	var scope struct{ Mode, App, Fwuid, PathPrefix, ContextPath string }
	if json.Unmarshal(cfg["context"], &scope) != nil || scope.Mode != "PROD" || scope.Fwuid == "" || len(scope.Fwuid) > 256 || scope.App != "siteforce:communityApp" || !strings.HasPrefix(base.Path, "/s/") || scope.ContextPath != "/s/sfsites" {
		return "", nil, fail
	}
	var assetURI string
	if json.Unmarshal(cfg["auraCmpDefBaseURI"], &assetURI) != nil {
		return "", nil, fail
	}
	relative, e := url.Parse(assetURI)
	if e != nil || relative.IsAbs() || relative.Host != "" || relative.User != nil || relative.Fragment != "" || relative.Path != "/auraCmpDef" {
		return "", nil, fail
	}
	asset, e := base.Parse(scope.ContextPath + assetURI)
	if e != nil {
		return "", nil, fail
	}
	args, e := url.ParseQuery(asset.RawQuery)
	if e != nil {
		return "", nil, fail
	}
	args.Set("_def", "markup://c:jobDetail")
	asset.RawQuery = args.Encode()
	var assetBody []byte
	for hop := 0; hop < 4; hop++ {
		assetBody, e = read("GET", asset.String(), "", false)
		if e == nil || redirectTarget == "" {
			break
		}
		next, parseErr := asset.Parse(redirectTarget)
		if parseErr != nil || next.Scheme != base.Scheme || next.Host != base.Host || next.User != nil || next.Fragment != "" || !strings.HasPrefix(next.Path, "/s/") {
			return "", nil, fail
		}
		asset = next
	}
	if e != nil {
		var p *policy.Reservation
		errors.As(e, &p)
		return "", p, e
	}
	labels := publicAuraLabel.FindAllSubmatch(assetBody, -1)
	if len(labels) != 1 {
		return "", nil, fail
	}
	var label string
	if json.Unmarshal(labels[0][1], &label) != nil || len(label) > 4096 {
		return "", nil, fail
	}
	var fullContext map[string]json.RawMessage
	if json.Unmarshal(cfg["context"], &fullContext) != nil {
		return "", nil, fail
	}
	requestContext := map[string]json.RawMessage{}
	for _, key := range []string{"mode", "fwuid", "app", "loaded", "uad"} {
		v, ok := fullContext[key]
		if !ok {
			return "", nil, fail
		}
		requestContext[key] = v
	}
	requestContext["dn"] = json.RawMessage("[]")
	requestContext["globals"] = json.RawMessage("{}")
	contextBody, e := json.Marshal(requestContext)
	if e != nil {
		return "", nil, fail
	}
	var requestName string
	for _, pair := range strings.Split(base.RawQuery, "&") {
		key, raw, ok := strings.Cut(pair, "=")
		if !ok {
			return "", nil, fail
		}
		if key == "jobReq" {
			if requestName != "" {
				return "", nil, fail
			}
			requestName, e = url.PathUnescape(raw)
			if e != nil || requestName == "" || len(requestName) > 1024 {
				return "", nil, fail
			}
		}
	}
	if requestName == "" {
		return "", nil, fail
	}
	endpoint := *base
	endpoint.Path = scope.ContextPath + "/aura"
	endpoint.RawPath = ""
	endpoint.RawQuery = "r=1&aura.ApexAction.execute=1"
	counter := 0
	rpc := func(method, param string, target any) error {
		switch method {
		case "getJobByJobReqTitleURL", "getDescription", "getLocation":
		default:
			return fail
		}
		counter++
		id := fmt.Sprintf("%d;a", counter)
		message := map[string]any{"actions": []any{map[string]any{"id": id, "descriptor": "aura://ApexActionController/ACTION$execute", "callingDescriptor": "UNKNOWN", "params": map[string]any{"namespace": "", "classname": "rolePage", "method": method, "params": map[string]string{"req": param}, "cacheable": true, "isContinuation": false}}}}
		raw, e := json.Marshal(message)
		if e != nil {
			return fail
		}
		form := url.Values{"message": {string(raw)}, "aura.context": {string(contextBody)}, "aura.pageURI": {base.RequestURI()}, "aura.token": {"null"}}
		response, e := read("POST", endpoint.String(), form.Encode(), false)
		if e != nil {
			return e
		}
		response = bytes.TrimSpace(bytes.TrimPrefix(bytes.TrimSpace(response), []byte("while(1);")))
		var envelope struct {
			Actions []struct {
				ID, State   string
				ReturnValue struct {
					Data json.RawMessage `json:"returnValue"`
				} `json:"returnValue"`
			}
		}
		if json.Unmarshal(response, &envelope) != nil || len(envelope.Actions) != 1 || envelope.Actions[0].ID != id || envelope.Actions[0].State != "SUCCESS" || len(envelope.Actions[0].ReturnValue.Data) == 0 {
			return fail
		}
		dec := json.NewDecoder(bytes.NewReader(envelope.Actions[0].ReturnValue.Data))
		dec.UseNumber()
		if dec.Decode(target) != nil {
			return fail
		}
		return nil
	}
	var job map[string]any
	if e = rpc("getJobByJobReqTitleURL", requestName, &job); e != nil {
		var p *policy.Reservation
		errors.As(e, &p)
		return "", p, e
	}
	jobID, ok := job["Id"].(string)
	if !ok || !regexp.MustCompile(`^[A-Za-z0-9]{15}(?:[A-Za-z0-9]{3})?$`).MatchString(jobID) {
		return "", nil, fail
	}
	var locations []map[string]any
	if e = rpc("getLocation", jobID, &locations); e != nil {
		var p *policy.Reservation
		errors.As(e, &p)
		return "", p, e
	}
	var description struct {
		HTML string `json:"Description__c"`
	}
	if e = rpc("getDescription", jobID, &description); e != nil {
		var p *policy.Reservation
		errors.As(e, &p)
		return "", p, e
	}
	document, e := composeAuraDescription(job, locations, description.HTML, label)
	if e != nil {
		return "", nil, e
	}
	if e = policy.Check(&runtimev1.ResourcePolicySignals{}, document, source); e != nil {
		var p *policy.Reservation
		errors.As(e, &p)
		return "", p, e
	}
	return document, nil, nil
}
