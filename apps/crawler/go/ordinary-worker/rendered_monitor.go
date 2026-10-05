package worker

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	htmltext "html"
	"net/url"
	"strings"

	"github.com/andybalholm/cascadia"
	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	lp "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/lightpandaclient"
	apisniffer "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
	executor "github.com/colophon-group/jobseek/apps/crawler/go/lightpanda-b0-executor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
	whatwg "github.com/nlnwa/whatwg-url/url"
	"golang.org/x/net/html"
)

type renderedMonitorClient interface {
	FetchMonitor(context.Context, queue.GreenhouseMonitorProfile, map[string]string) (RichDiscovery, error)
}

func (r *NativeRenderedDetails) FetchMonitor(ctx context.Context, profile queue.GreenhouseMonitorProfile, config map[string]string) (RichDiscovery, error) {
	result := RichDiscovery{Jobs: []RichMonitorJob{}}
	if r == nil || r.client == nil || queue.MonitorWorker(profile) != queue.Browser {
		return result, queue.ErrConfiguration
	}
	_, options, err := queue.RenderedDOMMonitorOptions(config)
	if err != nil {
		return result, err
	}
	select {
	case r.slots <- struct{}{}:
		defer func() { <-r.slots }()
	case <-ctx.Done():
		return result, ctx.Err()
	}
	for attempt := 0; attempt < 2; attempt++ {
		value, err := r.executeMonitorNavigation(ctx, profile, options, attempt)
		if err != nil {
			return result, err
		}
		result, err = parseHeldRenderedMonitor(ctx, profile, config, value)
		if attempt == 0 && errors.Is(err, executor.ErrBotChallenge) {
			continue
		}
		return result, err
	}
	return result, executor.ErrBotChallenge
}

// Navigation is shared by DOM and embedded document monitors. Parsing remains
// provider-specific and never grants queue/database authority.
func (r *NativeRenderedDetails) executeMonitorNavigation(ctx context.Context, profile queue.GreenhouseMonitorProfile, options map[string]any, attempt int) (*runtimev1.BrowserResult, error) {
	wait, fallback, timeout := "networkidle", "domcontentloaded", uint64(30000)
	if v, ok := options["wait"].(string); ok {
		wait = v
	}
	var fallbackWait *string = &fallback
	if v, present := options["wait_fallback"]; present {
		if v == nil {
			fallbackWait = nil
		} else {
			fallback = v.(string)
		}
	}
	if v, ok := options["timeout"].(float64); ok {
		timeout = uint64(v)
	}
	revision := profile.EffectiveConfigSHA256
	if v, ok := options["routing_revision"].(string); ok {
		revision = v
	}
	digest := sha256.Sum256([]byte(profile.BoardID + "|" + profile.EffectiveConfigSHA256 + "|" + profile.Endpoint))
	origin := "ordinary-monitor:" + hex.EncodeToString(digest[:])
	requestID := origin
	if attempt > 0 {
		requestID += ":challenge-retry-1"
	}
	input, err := lp.NavigationInput(lp.Navigation{URL: profile.Endpoint, RoutingRevision: revision, OriginRequestID: requestID, Wait: wait, WaitFallback: fallbackWait, TimeoutMS: timeout, TransportRetries: 1})
	if err != nil {
		return nil, err
	}
	held, err := waitRenderedReservation(ctx, r.client.Reserve)
	if err != nil {
		return nil, err
	}
	body, err := held.Execute(ctx, input)
	held.Close()
	if err != nil {
		return nil, err
	}
	value, err := executor.DecodeResult(body)
	if err != nil {
		return nil, err
	}

	return value, nil
}

func (r *NativeRenderedDetails) FetchNextdataPage(ctx context.Context, profile queue.GreenhouseMonitorProfile, config map[string]string, endpoint string) nextdataPage {
	result := nextdataPage{}
	if r == nil || r.client == nil || queue.MonitorWorker(profile) != queue.Browser || endpoint != profile.Endpoint {
		result.err = queue.ErrConfiguration
		return result
	}
	o, options, err := queue.RenderedNextdataMonitorOptions(config)
	if err != nil {
		result.err = err
		return result
	}
	select {
	case r.slots <- struct{}{}:
		defer func() { <-r.slots }()
	case <-ctx.Done():
		result.err = ctx.Err()
		return result
	}
	for attempt := 0; attempt < 2; attempt++ {
		value, err := r.executeMonitorNavigation(ctx, profile, options, attempt)
		if err != nil {
			result.err = err
			return result
		}
		result = parseHeldNextdataPage(ctx, profile, o, value)
		if attempt == 0 && errors.Is(result.err, executor.ErrBotChallenge) {
			continue
		}
		return result
	}
	result.err = executor.ErrBotChallenge
	return result
}

func parseHeldNextdataPage(ctx context.Context, profile queue.GreenhouseMonitorProfile, o apisniffer.NextdataOptions, value *runtimev1.BrowserResult) nextdataPage {
	result := nextdataPage{}
	if value == nil || value.GetSuccess() == nil || value.GetSuccess().ResourcePolicy == nil {
		result.err = policy.ErrSignals
		return result
	}
	source, err := executor.RenderedHTML(value, profile.Endpoint)
	if errors.Is(err, executor.ErrRenderedResult) || errors.Is(err, policy.ErrSignals) {
		result.err = err
		return result
	}
	success := value.GetSuccess()
	var reservation *policy.Reservation
	var statusError *executor.NavigationHTTPError
	if err != nil && !errors.As(err, &reservation) && !errors.As(err, &statusError) {
		result.err = err
		return result
	}
	requested, _ := url.Parse(profile.Endpoint)
	final, _ := url.Parse(success.FinalUrl)
	observation := httpObservation(ctx)
	observation.noteRequest(requested.Hostname())
	observation.noteResponse(final.Hostname(), int(success.GetStatus()))
	observation.noteBytes(int(success.Html.TotalSizeBytes))
	result.response = &GreenhouseResponse{endpoint: profile.Endpoint, finalURL: success.FinalUrl, status: int(success.GetStatus())}
	if reservation != nil {
		result.response.reserved = true
		result.response.policy = reservation.PolicyURL
		result.response.reservationSource = reservation.Source
		result.err = reservation
		return result
	}
	if err != nil {
		result.err = err
		return result
	}
	classification, err := dom.ClassifyRendered(source, map[string]any{}, success.FinalUrl)
	if err != nil {
		result.err = err
		return result
	}
	if classification["classification"] == "challenge" {
		result.err = executor.ErrBotChallenge
		return result
	}
	if o.ExpectedTitle != "" {
		m := nextdataTitle.FindStringSubmatch(source)
		if len(m) != 2 || strings.Join(strings.Fields(htmltext.UnescapeString(m[1])), " ") != o.ExpectedTitle {
			result.err = &DiscoveryError{Kind: "tenant_mismatch"}
			return result
		}
	}
	if o.BrowserDocumentTransform == "florida-courts" {
		result.document, result.err = apisniffer.ParseFloridaCourtsBrowserDocument(source)
	} else {
		result.document, result.err = apisniffer.ParseEmbeddedMonitorDocument(source, o.Source)
	}
	if result.err == nil {
		result.items, result.err = result.document.NextdataPageItems(o)
	}
	return result
}

func parseHeldRenderedMonitor(ctx context.Context, profile queue.GreenhouseMonitorProfile, config map[string]string, value *runtimev1.BrowserResult) (RichDiscovery, error) {
	result := RichDiscovery{Jobs: []RichMonitorJob{}}
	if value == nil || value.GetSuccess() == nil || value.GetSuccess().ResourcePolicy == nil {
		return result, policy.ErrSignals
	}
	source, err := executor.RenderedHTML(value, profile.Endpoint)
	if errors.Is(err, executor.ErrRenderedResult) || errors.Is(err, policy.ErrSignals) {
		return result, err
	}
	success := value.GetSuccess()
	var reservation *policy.Reservation
	var statusError *executor.NavigationHTTPError
	if err != nil && !errors.As(err, &reservation) && !errors.As(err, &statusError) {
		return result, err
	}
	requested, _ := url.Parse(profile.Endpoint)
	final, _ := url.Parse(success.FinalUrl)
	observation := httpObservation(ctx)
	observation.noteRequest(requested.Hostname())
	observation.noteResponse(final.Hostname(), int(success.GetStatus()))
	observation.noteBytes(int(success.Html.TotalSizeBytes))
	result.Response = &GreenhouseResponse{endpoint: profile.Endpoint, finalURL: success.FinalUrl, status: int(success.GetStatus())}
	if reservation != nil {
		result.Response.reserved = true
		result.Response.policy = reservation.PolicyURL
		result.Response.reservationSource = reservation.Source
		return result, nil
	}
	if err != nil {
		return result, err
	}
	listing, _, err := queue.RenderedDOMMonitorOptions(config)
	if err != nil {
		return result, err
	}
	result, err = parseDOMInventory(ctx, result, profile, listing, source, success.FinalUrl, true)
	return result, err
}

// Mirror DOM anchor.href: HTML5 selection, the first document base, and the
// browser URL serializer. Inventory identity must not use urllib's raw escapes.
func renderedListingURLs(source, finalURL, selector string) ([]string, error) {
	tree, err := html.ParseWithOptions(strings.NewReader(source), html.ParseOptionEnableScripting(true))
	if err != nil {
		return nil, err
	}
	// x/net/html stores template contents as children. In the browser they
	// belong to a separate DocumentFragment and are outside document queries.
	for _, node := range cascadia.QueryAll(tree, cascadia.MustCompile("template")) {
		if node.Namespace == "" {
			for node.FirstChild != nil {
				node.RemoveChild(node.FirstChild)
			}
		}
	}
	base := finalURL
	bases := cascadia.QueryAll(tree, cascadia.MustCompile("base[href]"))
	for _, node := range bases {
		if node.Namespace != "" {
			continue
		}
		for _, a := range node.Attr {
			if a.Key == "href" {
				if u, e := whatwg.ParseRef(finalURL, a.Val); e == nil {
					base = u.Href(false)
				}
				break
			}
		}
		break
	}
	if selector == "" {
		selector = "a[href]"
	}
	match, err := cascadia.Parse(selector)
	if err != nil {
		return nil, err
	}
	out := []string{}
	for _, node := range cascadia.QueryAll(tree, match) {
		if node.Namespace != "" || (node.Data != "a" && node.Data != "area") {
			return nil, queue.ErrConfiguration
		}
		for _, a := range node.Attr {
			if a.Key != "href" {
				continue
			}
			if u, e := whatwg.ParseRef(base, a.Val); e == nil {
				v := u.Href(false)
				if strings.HasPrefix(v, "http") {
					out = append(out, v)
				}
			}
			break
		}
	}
	return out, nil
}
