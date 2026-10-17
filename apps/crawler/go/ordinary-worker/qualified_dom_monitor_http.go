package worker

import (
	"context"
	"net/http"
	"net/url"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

type domHTTPMonitorRoute struct{ host, path string }

// These original browser configurations have complete matching public HTTP
// inventories. Keep their original parser, ownership hash and browser lane.
var qualifiedDOMHTTPMonitors = map[string]domHTTPMonitorRoute{
	"b87be4d4f12d20dcfdd071624a7501cd42f08e3ec7fa2daaf7eb6b9ebb48fbbb": {"securitasperu.pandape.computrabajo.com", "/Vacancies"},
	"46b92393e15abe43e0ef6b8f742b694b83a7de0e2c0089f53bd8f7b112f2e9d2": {"securitaschile.pandape.computrabajo.com", "/Vacancies"},
}

func qualifiedDOMHTTPMonitor(profile queue.GreenhouseMonitorProfile, config map[string]string) bool {
	route, ok := qualifiedDOMHTTPMonitors[profile.EffectiveConfigSHA256]
	if !ok || profile.Provider != "dom" || profile.Profile != "dom.rendered-urls/v1" ||
		profile.Endpoint != config["board_url"] || queue.MonitorWorker(profile) != queue.Browser {
		return false
	}
	u, err := url.Parse(profile.Endpoint)
	if err != nil || u.Scheme != "https" || u.User != nil || u.Opaque != "" || u.Fragment != "" ||
		u.RawQuery != "" || u.Hostname() != route.host || u.EscapedPath() != route.path ||
		(u.Port() != "" && u.Port() != "443") {
		return false
	}
	current, err := queue.InspectRichMonitor(profile.BoardID, config)
	return err == nil && current.Profile == profile.Profile && current.EffectiveConfigSHA256 == profile.EffectiveConfigSHA256
}

func discoverQualifiedDOMHTTPMonitor(ctx context.Context, verified *http.Client, profile queue.GreenhouseMonitorProfile, config map[string]string) (RichDiscovery, error) {
	if !qualifiedDOMHTTPMonitor(profile, config) || verified == nil {
		return RichDiscovery{}, queue.ErrConfiguration
	}
	listing, _, err := queue.RenderedDOMMonitorOptions(config)
	if err != nil || listing.Pagination != nil || listing.RequireJSONLD || listing.PDFProof != nil || listing.FetchURL != "" {
		return RichDiscovery{}, queue.ErrConfiguration
	}
	// Reuse verified HTTP, every redirect/resource policy check, bounded retry,
	// challenge rejection and the existing DOM inventory parser.
	return discoverDOMSingleInventory(ctx, verified, profile, listing, false, config)
}
