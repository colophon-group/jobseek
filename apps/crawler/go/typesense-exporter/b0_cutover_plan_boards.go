package main

import (
	"context"
	"encoding/json"
	"io"
	"math/big"
	"net/url"
	"sort"
	"strconv"
	"strings"

	"golang.org/x/net/idna"
)

var b0PlanIDNA = idna.New(idna.MapForLookup(), idna.Transitional(true), idna.ValidateLabels(false), idna.StrictDomainName(false))

func b0PlanDomain(source string) (string, error) {
	u, err := url.Parse(source)
	if err != nil || strings.ToLower(u.Scheme) != "https" || u.Hostname() == "" || u.User != nil || u.Fragment != "" {
		return "", errB0Cutover
	}
	if port := u.Port(); port != "" {
		n, err := strconv.Atoi(port)
		if err != nil || n != 443 {
			return "", errB0Cutover
		}
	}
	host := strings.ToLower(u.Hostname())
	ascii := true
	for _, r := range host {
		if r > 127 {
			ascii = false
			break
		}
	}
	if !ascii {
		host, err = b0PlanIDNA.ToASCII(host)
		if err != nil {
			return "", errB0Cutover
		}
		host = strings.ToLower(host)
	}
	labels := strings.Split(host, ".")
	if labels[len(labels)-1] == "" {
		labels = labels[:len(labels)-1]
	}
	for _, label := range labels {
		if len(label) == 0 || len(label) > 63 {
			return "", errB0Cutover
		}
	}
	return host, nil
}

func b0PlanMetadata(raw []byte) map[string]any {
	var value any
	decoder := json.NewDecoder(strings.NewReader(string(raw)))
	decoder.UseNumber()
	if decoder.Decode(&value) != nil {
		return map[string]any{}
	}
	if decoder.Decode(new(any)) != io.EOF {
		return map[string]any{}
	}

	if object, ok := value.(map[string]any); ok {
		return object
	}
	return map[string]any{}
}

// Retain Python dictionary equality for JSON numbers/bools, rather than
// treating equivalent PG/Redis number spellings as different configurations.
func b0PlanJSONEqual(a, b any) bool {
	number := func(value any) (*big.Rat, bool) {
		switch x := value.(type) {
		case json.Number:
			r, ok := new(big.Rat).SetString(string(x))
			return r, ok
		case bool:
			if x {
				return big.NewRat(1, 1), true
			}
			return big.NewRat(0, 1), true
		}
		return nil, false
	}
	if x, ok := number(a); ok {
		y, ok := number(b)
		return ok && x.Cmp(y) == 0
	}
	switch x := a.(type) {
	case nil:
		return b == nil
	case string:
		y, ok := b.(string)
		return ok && x == y
	case []any:
		y, ok := b.([]any)
		if !ok || len(x) != len(y) {
			return false
		}
		for n, v := range x {
			if !b0PlanJSONEqual(v, y[n]) {
				return false
			}
		}
		return true
	case map[string]any:
		y, ok := b.(map[string]any)
		if !ok || len(x) != len(y) {
			return false
		}
		for k, v := range x {
			other, ok := y[k]
			if !ok || !b0PlanJSONEqual(v, other) {
				return false
			}
		}
		return true
	}
	return false
}

func b0PlanBoolean(value string) bool {
	switch strings.ToLower(strings.TrimSpace(value)) {
	case "1", "true", "yes", "on":
		return true
	}
	return false
}
func b0PlanInterval(value string) int64 {
	n, err := strconv.ParseInt(strings.TrimSpace(value), 10, 64)
	if err != nil {
		return 24
	}
	return n
}

func (a *b0CutoverAdmin) validatedBoards(ctx context.Context, slugs []string) (map[string]b0PlanBoard, []map[string]any, error) {
	wanted := append([]string{}, slugs...)
	sort.Strings(wanted)
	for n, slug := range wanted {
		if slug == "" || n > 0 && wanted[n-1] == slug {
			return nil, nil, errB0Cutover
		}
	}
	rows, err := b0PlanReadBoards(ctx, a.pool, slugs)
	if err != nil || len(rows) != len(wanted) {
		return nil, nil, errB0Cutover
	}
	byID := map[string]b0PlanBoard{}
	documents := []map[string]any{}
	for n, row := range rows {
		interval := int64(24)
		if row.Interval != nil {
			interval = *row.Interval
		}
		if row.Slug != wanted[n] || row.ID == "" || row.URL == "" || row.CrawlerType == "" || row.Enabled == nil || !*row.Enabled || row.Status != "active" || row.Browser == nil || !*row.Browser || interval < 1 || interval > 8760 {
			return nil, nil, errB0Cutover
		}
		if _, exists := byID[row.ID]; exists {
			return nil, nil, errB0Cutover
		}
		metadata := b0PlanMetadata(row.Metadata)
		cached, err := a.queue.client.HGetAll(ctx, "board:"+row.ID).Result()
		if err != nil || len(cached) == 0 || cached["board_slug"] != row.Slug || cached["board_url"] != row.URL || cached["crawler_type"] != row.CrawlerType || !b0PlanBoolean(cached["scraper_needs_browser"]) || b0PlanInterval(cached["scrape_interval_hours"]) != interval || !b0PlanJSONEqual(metadata, b0PlanMetadata([]byte(cached["metadata"]))) {
			return nil, nil, errB0Cutover
		}
		byID[row.ID] = row
		documents = append(documents, map[string]any{"board_id": row.ID, "board_slug": row.Slug, "board_url": row.URL, "crawler_type": row.CrawlerType, "scraper_needs_browser": true, "scrape_interval_hours": interval, "metadata": metadata})
	}
	return byID, documents, nil
}

func b0PlanPostingFromPG(row b0PlanPostgresPosting) (b0PlanPosting, error) {
	value := b0PlanPosting{ID: row.ID, BoardID: row.BoardID, Hash: row.Hash, NextScrapeAt: row.Next, BoardStatus: row.Status}
	if row.URL != nil {
		value.SourceURL = *row.URL
	}
	if row.Active != nil {
		value.Active = *row.Active
	}
	if row.Enabled != nil {
		value.Enabled = *row.Enabled
	}
	if row.Browser != nil {
		value.Browser = *row.Browser
	}
	if row.LeaseActive != nil {
		value.LeaseActive = *row.LeaseActive
	}
	if row.Interval != nil {
		value.Interval = *row.Interval
	}
	domain, err := b0PlanDomain(value.SourceURL)
	if err != nil {
		return value, err
	}
	value.Domain = domain
	return value, nil
}
