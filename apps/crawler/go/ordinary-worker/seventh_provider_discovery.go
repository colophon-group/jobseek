package worker

import (
	"context"
	"errors"
	"net/http"
	"strconv"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	enrichment "github.com/colophon-group/jobseek/apps/crawler/go/job-enrichment"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func discoverSeventhProviderInventory(ctx context.Context, client *http.Client, p queue.GreenhouseMonitorProfile, config map[string]string) (RichDiscovery, error) {
	out := RichDiscovery{Jobs: []RichMonitorJob{}}
	o, e := api.SeventhProviderOptionsFromMetadata(p.Provider, config["board_url"], config["metadata"])
	if e != nil || client == nil || config["monitor_needs_browser"] != "0" || p.Profile != p.Provider+".public-items/v1" || p.Endpoint != o.ListingURL() {
		return out, queue.ErrConfiguration
	}
	sealed := *client
	// Only the pagination signal used by TRAFFIT is retained, privately and
	// per request. Every hop/body still passes the shared policy/transport.
	var pagesHeader string
	sealed.Transport = seventhHeaderTransport{base: client.Transport, observe: func(h string) { pagesHeader = h }}
	fetch := func(source string, headers http.Header) (*api.Document, error) {
		pagesHeader = ""
		raw, response, e := fetchProviderStatusResource(ctx, &sealed, o, source, nil, headers, 64<<20, nil, true)
		out.Response = response
		if e != nil {
			var d *DiscoveryError
			if errors.As(e, &d) && d.Kind == "provider_gone" {
				e = &DiscoveryError{Kind: "http_status", Status: d.Status}
			}
			return nil, e
		}
		return api.Decode(raw)
	}
	if o.Provider == "deel" && (o.Organization == "" || o.Board == "") {
		d, e := fetch(o.SettingsURL(), nil)
		if e == nil && (out.Response == nil || out.Response.status != 200) {
			e = &DiscoveryError{Kind: "settings_failed"}
		}
		if e != nil {
			return out, e
		}
		o, e = o.WithSettings(d)
		if e != nil {
			return out, e
		}
	}
	for page := 1; page <= 10000; page++ {
		headers := http.Header{}
		if o.Provider == "hibob" {
			headers.Set("Accept", "application/json")
			headers.Set("Referer", o.Origin+"/")
		}
		if o.Provider == "traffit" {
			headers.Set("X-Request-Page-Size", "100")
			headers.Set("X-Request-Current-Page", strconv.Itoa(page))
		}
		d, e := fetch(o.ListingURL(), headers)
		if e != nil {
			return RichDiscovery{Response: out.Response}, e
		}
		rows, e := d.SeventhProviderRows(o.Provider)
		if e != nil {
			return RichDiscovery{Response: out.Response}, e
		}
		for _, row := range rows {
			fields, e := d.SeventhProviderJobFields(row, o)
			if e != nil {
				return RichDiscovery{Response: out.Response}, e
			}
			if fields == nil {
				continue
			}
			if o.Provider == "hibob" {
				if raw, ok := fields["job_location_type"].(string); ok {
					kind := enrichment.NormalizeJobLocationType(raw)
					fields["job_location_type"] = nil
					if kind != "" {
						fields["job_location_type"] = kind
					}
				}
			}
			job, e := secondaryRichJob(fields)
			if e != nil {
				return RichDiscovery{Response: out.Response}, e
			}
			out.Jobs = append(out.Jobs, job)
		}
		out.Truncated = o.Provider != "deel" && len(out.Jobs) > 50000
		if o.Provider != "traffit" || len(rows) == 0 || pagesHeader == "" {
			return out, nil
		}
		total, e := strconv.Atoi(pagesHeader)
		// Python terminates on malformed/missing page headers. Retain its
		// inventory rather than silently inventing more requests.
		if e != nil || page >= total {
			return out, nil
		}
		if page == 10000 || out.Truncated {
			out.Truncated = true
			return out, nil
		}
	}
	return out, api.ErrInventory
}

type seventhHeaderTransport struct {
	base    http.RoundTripper
	observe func(string)
}

func (t seventhHeaderTransport) RoundTrip(r *http.Request) (*http.Response, error) {
	base := t.base
	if base == nil {
		base = http.DefaultTransport
	}
	response, e := base.RoundTrip(r)
	if response != nil {
		t.observe(response.Header.Get("X-Result-Total-Pages"))
	}
	return response, e
}
