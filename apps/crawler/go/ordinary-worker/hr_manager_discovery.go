package worker

import (
	"context"
	"encoding/json"
	"errors"
	htmltext "html"
	"io"
	"net/http"
	"strings"

	"github.com/andybalholm/cascadia"
	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
	jsonld "github.com/colophon-group/jobseek/apps/crawler/go/jsonld-detail"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	"golang.org/x/net/html"
)

var errHRManagerInventory = errors.New("HR Manager position/feed inventory mismatch")

func hrManagerPositions(source, customer string) (map[string]map[string]any, error) {
	doc, err := html.Parse(strings.NewReader(source))
	if err != nil {
		return nil, err
	}
	node := cascadia.Query(doc, cascadia.MustCompile(`input[id$="HiddenField_PositionList"]`))
	raw := ""
	if node != nil {
		for _, attr := range node.Attr {
			if attr.Key == "value" {
				raw = htmltext.UnescapeString(attr.Val)
				break
			}
		}
	}
	var payload map[string]any
	decoder := json.NewDecoder(strings.NewReader(raw))
	decoder.UseNumber()
	if raw == "" || decoder.Decode(&payload) != nil {
		return nil, errHRManagerInventory
	}
	var tail any
	if decoder.Decode(&tail) != io.EOF {
		return nil, errHRManagerInventory
	}
	list, ok := payload["PositionList"].(map[string]any)
	if !ok {
		return nil, errHRManagerInventory
	}
	alias := list["CustomerAlias"]
	if alias == nil || alias == "" {
		alias = payload["CustomerAlias"]
	}
	name, ok := alias.(string)
	if !ok || strings.ToLower(name) != customer {
		return nil, errHRManagerInventory
	}
	status, ok := list["TransactionStatus"].(map[string]any)
	if !ok || len(status) == 0 {
		status, _ = payload["TransactionStatus"].(map[string]any)
	}
	code, ok := status["StatusCode"].(json.Number)
	if !ok || code != "0" {
		return nil, errHRManagerInventory
	}
	items, ok := list["Items"].([]any)
	count, countOK := list["PositionCountList"].(json.Number)
	if !ok || !countOK {
		return nil, errHRManagerInventory
	}
	n, err := count.Int64()
	if err != nil || n != int64(len(items)) || len(items) >= 50000 {
		return nil, errHRManagerInventory
	}
	positions := map[string]map[string]any{}
	for _, raw := range items {
		item, ok := raw.(map[string]any)
		if !ok || item["ProjectType"] != "RecruitmentProject" {
			return nil, errHRManagerInventory
		}
		id, ok := item["Id"].(json.Number)
		if !ok {
			return nil, errHRManagerInventory
		}
		n, err := id.Int64()
		if err != nil || n <= 0 || positions[id.String()] != nil {
			return nil, errHRManagerInventory
		}
		positions[id.String()] = item
	}
	return positions, nil
}

func hrManagerLocations(position map[string]any) []string {
	if place, ok := position["WorkPlace"].(string); ok && strings.TrimSpace(place) != "" {
		return []string{strings.TrimSpace(place)}
	}
	values := []string{}
	seen := map[string]bool{}
	if many, ok := position["PositionLocationMultiSelection"].([]any); ok {
		for _, raw := range many {
			item, _ := raw.(map[string]any)
			name, _ := item["Name"].(string)
			name = strings.TrimSpace(name)
			if name != "" && !seen[name] {
				seen[name] = true
				values = append(values, name)
			}
		}
	}
	if len(values) == 0 {
		item, _ := position["PositionLocation"].(map[string]any)
		name, _ := item["Name"].(string)
		if name = strings.TrimSpace(name); name != "" {
			values = append(values, name)
		}
	}
	if len(values) == 0 {
		return nil
	}
	return values
}

func discoverHRManager(ctx context.Context, client *http.Client, profile queue.GreenhouseMonitorProfile, config map[string]string) (RichDiscovery, error) {
	c, err := queue.HRManagerOptions(config)
	if err != nil || profile.Endpoint != c.Feed {
		return RichDiscovery{}, queue.ErrConfiguration
	}
	options, err := dom.ListingOptions(dom.Object{}, c.Board)
	if err != nil {
		return RichDiscovery{}, err
	}
	// Preserve the legacy board fetch policy and its two-million-codepoint
	// preview. The feed and manifest must agree before returning any jobs.
	var source string
	var observed *GreenhouseResponse
	for attempt := 0; attempt < options.Attempts; attempt++ {
		doc, failure := jsonld.FetchDocumentWithClient(ctx, c.Board, options.Document, client)
		if doc.Responses > 0 {
			var policy *string
			if doc.TDMPolicy != "" {
				value := doc.TDMPolicy
				policy = &value
			}
			observed = &GreenhouseResponse{endpoint: c.Board, finalURL: doc.FinalURL, status: doc.Status, reserved: doc.ErrorKind == "tdm", policy: policy, reservationSource: doc.TDMSource}
		}
		if failure == nil && doc.Status == 200 && len(doc.Body) > 0 {
			source = jsonld.DecodeDocument(doc.Body, doc.ContentType)
			break
		}
		if ctx.Err() != nil || observed != nil && observed.reserved || attempt == options.Attempts-1 {
			return RichDiscovery{Response: observed}, errHRManagerInventory
		}
		if err := almaRetry(ctx, attempt); err != nil {
			return RichDiscovery{Response: observed}, err
		}
	}
	count := 0
	for offset := range source {
		if count == 2_000_000 {
			source = source[:offset]
			break
		}
		count++
	}
	positions, err := hrManagerPositions(source, c.Customer)
	if err != nil {
		return RichDiscovery{Response: observed}, err
	}
	feed, err := discoverGenericRSS(ctx, client, profile)
	if err != nil {
		return feed, err
	}
	// The feed parser cannot establish the board join itself. Preserve original
	// item IDs and validate both complete sets before granting absence handling.
	return attachHRManagerFeed(positions, feed, c.Customer)
}

func attachHRManagerPositions(source string, feed RichDiscovery, customer string) (RichDiscovery, error) {
	positions, err := hrManagerPositions(source, customer)
	if err != nil || feed.Truncated {
		return RichDiscovery{Response: feed.Response}, errHRManagerInventory
	}
	return attachHRManagerFeed(positions, feed, customer)
}

func attachHRManagerFeed(positions map[string]map[string]any, feed RichDiscovery, customer string) (RichDiscovery, error) {
	seen := map[string]bool{}
	for index := range feed.Jobs {
		job := &feed.Jobs[index]
		id, _ := job.Metadata["id"].(string)
		position := positions[id]
		if position == nil || seen[id] {
			return RichDiscovery{Response: feed.Response}, errHRManagerInventory
		}
		seen[id] = true
		job.Locations = hrManagerLocations(position)
		custom, _ := position["CustomList1"].(map[string]any)
		if employment, ok := custom["Name"].(string); ok && strings.TrimSpace(employment) != "" {
			job.EmploymentType = strings.TrimSpace(employment)
		}
		if languages, ok := position["Languages"].([]any); ok && len(languages) > 0 {
			first, _ := languages[0].(map[string]any)
			if code, ok := first["Code"].(string); ok {
				code = strings.ToLower(code)
				if len(code) == 2 && code[0] >= 'a' && code[0] <= 'z' && code[1] >= 'a' && code[1] <= 'z' {
					job.Language = code
				}
			}
		}
		job.SourceIdentity = "hr_manager:" + customer + ":" + id
	}
	if len(seen) != len(positions) {
		return RichDiscovery{Response: feed.Response}, errHRManagerInventory
	}
	return feed, nil
}
