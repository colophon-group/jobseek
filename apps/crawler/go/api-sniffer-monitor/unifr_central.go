package apisniffer

import (
	"encoding/json"
	"fmt"
	"sort"
	"strconv"
	"strings"
	"time"

	"golang.org/x/net/html"
)

func unifrText(node *html.Node) string { return inlineNormalized(paylocityText(node, "")) }

func unifrPage(tree *html.Node, suffix, heading string) error {
	title := eighthFirst(tree, "title")
	if title == nil || !strings.HasSuffix(unifrText(title), suffix) {
		return ErrInventory
	}
	count := 0
	for _, node := range eighthSelect(tree, "main h1, main h2") {
		if unifrText(node) == heading {
			count++
		}
	}
	if count != 1 || len(eighthSelect(tree, `main .pagination, main ul.pagination, main a[rel="next"], link[rel="next"]`)) != 0 {
		return ErrInventory
	}
	return nil
}

func UnifrCentralListing(body []byte, locale string) (map[string]string, error) {
	tree, err := eighthTree(body)
	if err != nil {
		return nil, err
	}
	heading, suffix := "Postes vacants - Offres d'emploi à l'Université de Fribourg", "| Service du personnel | Université de Fribourg"
	if locale == "de" {
		heading, suffix = "Offene Stellen - Stellenangebote an der Universität Freiburg", "| Personaldienst | Universität Freiburg"
	} else if locale != "fr" {
		return nil, ErrOptions
	}
	if unifrPage(tree, suffix, heading) != nil || len(eighthSelect(tree, "main ul.list-group.list")) != 1 {
		return nil, ErrInventory
	}
	nodes := eighthSelect(tree, "main ul.list-group.list > li.list-group-item")
	if len(nodes) == 0 || len(nodes) > 100 {
		return nil, ErrInventory
	}
	out := map[string]string{}
	for _, node := range nodes {
		id := eighthAttr(node, "id")
		if !unifrNumericID.MatchString(id) {
			return nil, ErrInventory
		}
		titles, controls := eighthSelect(node, "h4.list-group-item-heading.name"), eighthSelect(node, "div#open"+id)
		if len(titles) != 1 || len(controls) != 1 {
			return nil, ErrInventory
		}
		title := unifrText(titles[0])
		if title == "" {
			return nil, ErrInventory
		}
		if _, exists := out[id]; exists {
			return nil, ErrInventory
		}
		out[id] = title
	}
	return out, nil
}

func unifrTimestamp(raw string) (time.Time, string, error) {
	value, err := time.Parse(time.RFC3339Nano, raw)
	if err != nil {
		return value, "", ErrInventory
	}
	// datetime.fromisoformat truncates sub-microsecond precision and isoformat
	// prints six fraction digits only when a nonzero microsecond is present.
	value = value.Truncate(time.Microsecond)
	formatted := value.Format("2006-01-02T15:04:05")
	if value.Nanosecond() != 0 {
		formatted += fmt.Sprintf(".%06d", value.Nanosecond()/1000)
	}
	formatted += value.Format("-07:00")
	return value, formatted, nil
}

func UnifrCentralDetail(body []byte, id, locale, listingTitle string, today time.Time) (map[string]string, error) {
	d, err := Decode(body)
	if err != nil {
		return nil, err
	}
	p, ok := d.Value.(map[string]any)
	if !ok || !unifrNumericID.MatchString(id) {
		return nil, ErrInventory
	}
	providerID, ok := p["id"].(string)
	if !ok {
		if number, ok := p["id"].(json.Number); ok {
			if _, err := number.Int64(); err == nil {
				providerID = number.String()
			}
		}
	}
	listing, owner := UnifrCentralFR, "Université"
	if locale == "de" {
		listing, owner = UnifrCentralDE, "Universität"
	} else if locale != "fr" {
		return nil, ErrOptions
	}
	if providerID != id || p["autorite"] != owner || p["link"] != listing+"#"+id {
		return nil, ErrInventory
	}
	out := map[string]string{}
	for _, key := range []string{"fonction", "content", "startpublish", "endpublish"} {
		value, ok := p[key].(string)
		if !ok || strings.TrimSpace(value) == "" {
			return nil, ErrInventory
		}
		out[key] = strings.TrimSpace(value)
	}
	if inlineNormalized(out["fonction"]) != listingTitle {
		return nil, ErrInventory
	}
	start, formatted, err := unifrTimestamp(out["startpublish"])
	if err != nil {
		return nil, err
	}
	end, _, err := unifrTimestamp(out["endpublish"])
	if err != nil || end.Before(start) || end.UTC().Format("2006-01-02") < today.UTC().Format("2006-01-02") {
		return nil, ErrInventory
	}
	out["startpublish"], out["endpublish"] = formatted, end.Format("2006-01-02")
	return out, nil
}

type UnifrJob struct {
	URL           string                    `json:"url"`
	Title         string                    `json:"title"`
	Description   string                    `json:"description"`
	DatePosted    string                    `json:"date_posted"`
	Language      string                    `json:"language"`
	Locations     []string                  `json:"locations"`
	Localizations map[string]map[string]any `json:"localizations"`
	Extras        map[string]any            `json:"extras"`
	Metadata      map[string]any            `json:"metadata"`
	URLOnly       bool                      `json:"url_only"`
}

func UnifrCentralJobs(details map[string]map[string]map[string]string) ([]UnifrJob, error) {
	if len(details) == 0 || len(details) > 200 {
		return nil, ErrInventory
	}
	ids := make([]string, 0, len(details))
	for id := range details {
		if !unifrNumericID.MatchString(id) {
			return nil, ErrInventory
		}
		ids = append(ids, id)
	}
	sort.Slice(ids, func(i, j int) bool {
		left, _ := strconv.ParseUint(ids[i], 10, 64)
		right, _ := strconv.ParseUint(ids[j], 10, 64)
		return left < right
	})
	out := make([]UnifrJob, 0, len(ids))
	for _, id := range ids {
		locales := details[id]
		primary := "fr"
		if _, ok := locales[primary]; !ok {
			primary = "de"
		}
		p, ok := locales[primary]
		if !ok {
			return nil, ErrInventory
		}
		localized := map[string]map[string]any{}
		for locale, values := range locales {
			if locale != "fr" && locale != "de" || values["fonction"] == "" || values["content"] == "" || values["startpublish"] == "" || values["endpublish"] == "" {
				return nil, ErrInventory
			}
			localized[locale] = map[string]any{"title": values["fonction"], "description": values["content"], "locations": []string{"Fribourg, Switzerland"}}
		}
		source, err := unifrJobURL(UnifrCentralFR, id)
		if err != nil {
			return nil, err
		}
		out = append(out, UnifrJob{URL: source, Title: p["fonction"], Description: p["content"], Locations: []string{"Fribourg, Switzerland"}, Language: primary, DatePosted: p["startpublish"], Localizations: localized, Extras: map[string]any{"valid_through": p["endpublish"]}, Metadata: map[string]any{"unifr_provider_id": id}})
	}
	return out, nil
}
