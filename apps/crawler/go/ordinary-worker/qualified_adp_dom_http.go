package worker

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	b0task "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/b0task"
	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
	executor "github.com/colophon-group/jobseek/apps/crawler/go/lightpanda-b0-executor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
	"net/http"
	"net/http/cookiejar"
	"net/url"
	"strings"
)

// This unchanged rendered DOM configuration has matching original/public-API
// references. The public ADP reader supplies the document to the original parser;
// claim, fingerprint, field selection and Browser queue authority stay unchanged.
var qualifiedADPDOMHTTPDetails = map[string]string{
	// hidden-level-adp: full canonical fingerprint -> public client/center scope.
	"2beaff6201e4d271849840c54129e187aacd3cc115bef4fa492b37394dde271a": "be347749599899584e4df53751bdadaa778121648aed44e8ec72d0efe8d205f7",
}

func qualifiedADPDOMHTTPDetail(p queue.WorkdayDetailProfile) bool {
	scope, ok := qualifiedADPDOMHTTPDetails[p.EffectiveBoardSHA256]
	if !ok || p.Profile != "dom.rendered-detail/v1" || p.SkipSSL {
		return false
	}
	u, e := url.Parse(p.SourceURL)
	if e != nil || u.Scheme != "https" || u.User != nil || u.Fragment != "" || u.Hostname() != "workforcenow.adp.com" || (u.Port() != "" && u.Port() != "443") || !strings.HasPrefix(u.EscapedPath(), "/mascsr/default/mdf/recruitment/") {
		return false
	}
	for _, part := range strings.Split(u.Path, "/") {
		if part == "." || part == ".." {
			return false
		}
	}
	query, e := url.ParseQuery(u.RawQuery)
	if e != nil {
		return false
	}
	for _, key := range []string{"cid", "ccId", "jobId", "itemId", "lang", "locale"} {
		if len(query[key]) > 1 {
			return false
		}
	}
	sum := sha256.Sum256([]byte(query.Get("cid") + "\n" + query.Get("ccId")))
	if hex.EncodeToString(sum[:]) != scope {
		return false
	}
	_, e = api.ADPDetailRoute(p.SourceURL, nil)
	return e == nil
}

func fetchQualifiedADPDOMHTTP(ctx context.Context, verified *VerifiedDirectHTTP, p queue.WorkdayDetailProfile) (map[string]any, *policy.Reservation, error) {
	if !qualifiedADPDOMHTTPDetail(p) || verified == nil || verified.client == nil || verified.proxyRequired || verified.skipSSL {
		return nil, nil, queue.ErrConfiguration
	}
	client := *verified.client
	jar, err := cookiejar.New(nil)
	if err != nil {
		return nil, nil, err
	}
	client.Jar = jar
	client.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	content, reserved, e := fetchADPDetail(ctx, &client, p)
	if e != nil || reserved != nil {
		return nil, reserved, e
	}
	description, _ := content["description"].(string)
	if description == "" {
		return nil, nil, executor.ErrEmptyResult
	}
	document := "<!doctype html><html><body>" + description + "</body></html>"
	if e = policy.Check(&runtimev1.ResourcePolicySignals{}, document, p.SourceURL); e != nil {
		if errors.As(e, &reserved) {
			return nil, reserved, nil
		}
		return nil, nil, e
	}
	raw, e := json.Marshal(p.DOMConfig)
	if e != nil {
		return nil, nil, e
	}
	value, e := b0task.ParseCanonicalValue(raw)
	if e != nil {
		return nil, nil, e
	}
	options, ok := value.(map[string]any)
	if !ok {
		return nil, nil, queue.ErrConfiguration
	}
	fields, e := dom.Parse(document, dom.Object(options), &p.SourceURL)
	return fields, nil, e
}
