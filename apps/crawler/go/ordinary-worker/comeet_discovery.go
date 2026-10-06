package worker

import (
	"context"
	"errors"
	"fmt"
	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	enrichment "github.com/colophon-group/jobseek/apps/crawler/go/job-enrichment"
	jsonld "github.com/colophon-group/jobseek/apps/crawler/go/jsonld-detail"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	"net/http"
	"unicode/utf8"
)

func discoverComeetInventory(ctx context.Context, client *http.Client, p queue.GreenhouseMonitorProfile, config map[string]string) (RichDiscovery, error) {
	out := RichDiscovery{Jobs: []RichMonitorJob{}}
	o, e := api.ComeetOptionsFromMetadata(config["board_url"], config["metadata"])
	profile := "comeet.hosted-items/v1"
	if o.API {
		profile = "comeet.api-items/v1"
	}
	if e != nil || client == nil || p.Provider != "comeet" || p.Profile != profile || p.Endpoint != o.Endpoint || config["monitor_needs_browser"] != "0" {
		return out, queue.ErrConfiguration
	}
	raw, response, e := fetchProviderStatusResource(ctx, client, o, o.Endpoint, nil, nil, 64<<20, nil, o.API)
	out.Response = response
	if e != nil {
		var f *DiscoveryError
		if !o.API && errors.As(e, &f) && f.Kind == "provider_gone" {
			e = &DiscoveryError{Kind: "http_status", Status: f.Status}
		}
		return out, e
	}
	if !o.API {
		text := jsonld.DecodeDocument(raw, "")
		if utf8.RuneCountInString(text) > 20_000_000 {
			r := []rune(text)
			text = string(r[:20_000_000])
		}
		raw = []byte(text)
	}
	rows, e := api.ComeetPositions(raw, o)
	if e != nil {
		return out, e
	}
	for _, row := range rows {
		if ctx.Err() != nil {
			return out, ctx.Err()
		}
		fields := api.ComeetFields(row)
		if fields == nil {
			continue
		}
		if v := fields["job_location_type"]; v != nil {
			normalized := enrichment.NormalizeJobLocationType(fmt.Sprint(v))
			fields["job_location_type"] = nil
			if normalized != "" {
				fields["job_location_type"] = normalized
			}
		}
		job, e := secondaryRichJob(fields)
		if e != nil {
			return RichDiscovery{Response: out.Response}, e
		}
		out.Jobs = append(out.Jobs, job)
	}
	out.Truncated = len(out.Jobs) > 50000
	return out, nil
}
