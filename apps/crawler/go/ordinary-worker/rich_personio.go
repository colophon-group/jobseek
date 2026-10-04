package worker

import (
	"context"
	"net/http"
	"net/url"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	personio "github.com/colophon-group/jobseek/apps/crawler/go/personio-monitor"
)

// Personio probes its two documented domains and configured languages. The
// original claim still binds the complete source configuration on every write.
func personioResponseMatches(profile queue.GreenhouseMonitorProfile, endpoint string) bool {
	u, err := url.Parse(endpoint)
	if err != nil || u.Scheme != "https" || u.User != nil || u.Fragment != "" || u.Opaque != "" {
		return false
	}
	if u.Host != profile.Token+".jobs.personio.de" && u.Host != profile.Token+".jobs.personio.com" {
		return false
	}
	if u.Path == "/" && u.RawQuery == "" {
		return true
	}
	if u.Path != "/xml" {
		return false
	}
	for _, language := range append([]string{profile.Language}, profile.BackfillLanguages...) {
		if u.RawQuery == "language="+language {
			return true
		}
	}
	return false
}

func discoverPersonioRich(ctx context.Context, client *http.Client, profile queue.GreenhouseMonitorProfile) (RichDiscovery, error) {
	observed := &rssRichClient{client: client, profile: profile}
	found, err := personio.Fetch(ctx, observed, profile.Token, profile.Region, profile.Language, profile.BackfillLanguages)
	if ctx.Err() != nil {
		return RichDiscovery{}, ctx.Err()
	}
	if err != nil {
		if observed.response != nil && observed.response.reserved {
			return RichDiscovery{Response: observed.response}, &DiscoveryError{Kind: "publisher_reserved", Status: observed.response.status}
		}
		return RichDiscovery{}, &DiscoveryError{Kind: "feed_failed"}
	}
	result := RichDiscovery{Jobs: []RichMonitorJob{}, Truncated: found.Truncated}
	// Backfill misses are optional. Retain the actual primary response rather
	// than treating the last optional 404 as evidence about the whole board.
	for _, response := range observed.responses {
		u, parseErr := url.Parse(response.endpoint)
		if parseErr == nil && (u.Path == "/" || u.RawQuery == "language="+profile.Language) && response.finalURL == found.FinalURL && response.status == found.Status {
			result.Response = response
		}
	}
	if result.Response == nil {
		return RichDiscovery{}, &DiscoveryError{Kind: "invalid_response"}
	}
	for _, job := range found.Jobs {
		var language, employment, posted any
		if job.Language != nil {
			language = *job.Language
		}
		if job.EmploymentType != nil {
			employment = *job.EmploymentType
		}
		if job.DatePosted != nil {
			posted = *job.DatePosted
		}
		rich := RichMonitorJob{URL: job.URL, Title: job.Title, Description: job.Description, Locations: job.Locations, Language: language, EmploymentType: employment, DatePosted: posted, Metadata: job.Metadata}
		// Python dictionaries keep the primary locale first, even when English
		// is promoted. Iterate that configured order instead of Go map order.
		seen := map[string]bool{}
		for _, locale := range append([]string{profile.Language}, profile.BackfillLanguages...) {
			fields, ok := job.Localizations[locale].(map[string]any)
			if !ok || seen[locale] {
				continue
			}
			seen[locale] = true
			rich.LocalizationLocales = append(rich.LocalizationLocales, locale)
			if title, ok := fields["title"].(string); ok && title != "" {
				rich.LocalizedTitles = append(rich.LocalizedTitles, title)
			}
		}
		result.Jobs = append(result.Jobs, rich)
	}
	return result, nil
}
