package apisniffer

import (
	"encoding/json"
	"html"
	"net/url"
	"regexp"
	"strings"
	"time"

	"golang.org/x/text/cases"
)

type UKGBoard struct{ Host, Tenant, BoardID string }

var ukgHost = regexp.MustCompile(`^(?:recruiting(?:[2-9])?\.ultipro\.com|recruiting\.ultipro\.ca|[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.rec\.pro\.ukg\.net)$`)
var ukgTenant = regexp.MustCompile(`^[A-Za-z0-9]{3,64}$`)
var ukgUUID = regexp.MustCompile(`(?i)^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$`)

func normalizeUKGUUID(value any) string {
	s, ok := value.(string)
	if !ok {
		return ""
	}
	s = strings.TrimSpace(s)
	if !ukgUUID.MatchString(s) {
		return ""
	}
	return strings.ToLower(s)
}
func UKGBoardFromURL(source string) (UKGBoard, error) {
	u, err := url.Parse(source)
	if err != nil || len(source) > 4096 || !validURL(source) || u.Scheme != "https" || u.Fragment != "" {
		return UKGBoard{}, ErrOptions
	}
	host := strings.TrimRight(strings.ToLower(u.Hostname()), ".")
	parts := []string{}
	for _, p := range strings.Split(u.Path, "/") {
		if p != "" {
			parts = append(parts, p)
		}
	}
	if !ukgHost.MatchString(host) || len(parts) != 3 && len(parts) != 4 || !ukgTenant.MatchString(parts[0]) || !strings.EqualFold(parts[1], "jobboard") || normalizeUKGUUID(parts[2]) == "" {
		return UKGBoard{}, ErrOptions
	}
	query, err := url.ParseQuery(u.RawQuery)
	if err != nil {
		return UKGBoard{}, ErrOptions
	}
	if len(parts) == 3 && len(query) != 0 {
		return UKGBoard{}, ErrOptions
	}
	if len(parts) == 4 {
		if !strings.EqualFold(parts[3], "opportunitydetail") || len(query) != 1 {
			return UKGBoard{}, ErrOptions
		}
		valid := false
		for k, v := range query {
			valid = strings.EqualFold(k, "opportunityid") && len(v) == 1 && normalizeUKGUUID(v[0]) != ""
		}
		if !valid {
			return UKGBoard{}, ErrOptions
		}
	}
	return UKGBoard{host, parts[0], normalizeUKGUUID(parts[2])}, nil
}
func UKGOptionsFromMetadata(source, raw string) (UKGBoard, error) {
	md, err := DecodeInlineMetadata(raw)
	if err != nil {
		return UKGBoard{}, err
	}
	for _, key := range []string{"proxy", "render", "skip_ssl", "actions"} {
		if detailTruthy(md[key]) {
			return UKGBoard{}, ErrOptions
		}
	}
	if md["ssl_verify"] != nil && md["ssl_verify"] != true {
		return UKGBoard{}, ErrOptions
	}
	if listing, ok := md["listing_url"].(string); ok {
		if b, err := UKGBoardFromURL(listing); err == nil {
			return b, nil
		}
	}
	host, _ := md["host"].(string)
	tenant, _ := md["tenant"].(string)
	host = strings.TrimRight(strings.ToLower(strings.TrimSpace(host)), ".")
	tenant = strings.TrimSpace(tenant)
	id := normalizeUKGUUID(md["board_id"])
	if ukgHost.MatchString(host) && ukgTenant.MatchString(tenant) && id != "" {
		return UKGBoard{host, tenant, id}, nil
	}
	return UKGBoardFromURL(source)
}
func (b UKGBoard) ListingURL() string {
	return "https://" + b.Host + "/" + b.Tenant + "/JobBoard/" + b.BoardID
}
func (b UKGBoard) SearchURL() string { return b.ListingURL() + "/JobBoardView/LoadSearchResults" }
func (b UKGBoard) JobURL(id string) string {
	return b.ListingURL() + "/OpportunityDetail?opportunityId=" + id
}
func (b UKGBoard) ResourceMatches(source string) bool { return source == b.SearchURL() }
func UKGSearchPayload(skip, take int) ([]byte, error) {
	if skip < 0 || skip > 50000 || take < 1 || take > 100 {
		return nil, ErrOptions
	}
	return json.Marshal(map[string]any{"opportunitySearch": map[string]any{"Top": take, "Skip": skip, "QueryString": "", "Filters": []any{}}})
}
func UKGPage(d *Document) (int64, []any, error) {
	raw, ok := d.Value.(map[string]any)
	if !ok {
		return 0, nil, ErrInventory
	}
	n, ok := raw["totalCount"].(json.Number)
	if !ok {
		return 0, nil, ErrInventory
	}
	total, err := n.Int64()
	if err != nil || total < 0 {
		return 0, nil, ErrInventory
	}
	rows, ok := raw["opportunities"].([]any)
	if !ok {
		return 0, nil, ErrInventory
	}
	return total, rows, nil
}
func ukgClean(value any) string {
	s, ok := value.(string)
	if !ok {
		return ""
	}
	return strings.Join(strings.Fields(html.UnescapeString(s)), " ")
}
func ukgLocations(value any) any {
	rows, ok := value.([]any)
	if !ok {
		return nil
	}
	out := []string{}
	seen := map[string]bool{}
	fold := cases.Fold()
	for _, value := range rows {
		raw, ok := value.(map[string]any)
		if !ok {
			continue
		}
		address, _ := raw["Address"].(map[string]any)
		parts := []string{}
		partSeen := map[string]bool{}
		for _, key := range []string{"City", "State", "Country"} {
			value := address[key]
			if m, ok := value.(map[string]any); ok {
				value = m["Name"]
				if !detailTruthy(value) {
					value = m["Code"]
				}
			}
			part := ukgClean(value)
			if part != "" && !partSeen[fold.String(part)] {
				partSeen[fold.String(part)] = true
				parts = append(parts, part)
			}
		}
		location := strings.Join(parts, ", ")
		if location == "" {
			location = ukgClean(raw["LocalizedName"])
			if location == "" {
				location = ukgClean(raw["LocalizedDescription"])
			}
		}
		if location != "" && !seen[fold.String(location)] {
			seen[fold.String(location)] = true
			out = append(out, location)
		}
	}
	if len(out) == 0 {
		return nil
	}
	return out
}
func UKGFields(raw any, b UKGBoard) map[string]any {
	row, ok := raw.(map[string]any)
	if !ok {
		return nil
	}
	id, title := normalizeUKGUUID(row["Id"]), ukgClean(row["Title"])
	if id == "" || title == "" {
		return nil
	}
	var employment, locationType, date, description any
	if value, ok := row["FullTime"].(bool); ok {
		employment = "part_time"
		if value {
			employment = "full_time"
		}
	}
	if value, ok := row["JobLocationType"].(json.Number); ok {
		if n, err := value.Int64(); err == nil {
			locationType = map[int64]string{0: "hybrid", 1: "onsite", 2: "remote"}[n]
			if locationType == "" {
				locationType = nil
			}
		}
	}
	if value, ok := row["BriefDescription"].(string); ok {
		description = value
	}
	if value := ukgClean(row["PostedDate"]); value != "" {
		for _, layout := range []string{time.RFC3339Nano, "2006-01-02T15:04:05.999999999", "2006-01-02"} {
			if t, err := time.Parse(layout, value); err == nil {
				date = t.Format("2006-01-02")
				break
			}
		}
	}
	metadata := map[string]any{"opportunity_id": id}
	for source, target := range map[string]string{"RequisitionNumber": "requisition_number", "JobCategoryName": "category", "OpportunityType": "opportunity_type"} {
		value := row[source]
		switch v := value.(type) {
		case string:
			if v != "" {
				metadata[target] = v
			}
		case json.Number:
			if _, err := v.Int64(); err == nil {
				metadata[target] = v
			}
		}
	}
	// Description normalization is performed by the existing canonical preparer.
	return map[string]any{"url": b.JobURL(id), "title": title, "description": description, "locations": ukgLocations(row["Locations"]), "employment_type": employment, "job_location_type": locationType, "date_posted": date, "metadata": metadata, "language": nil, "extras": nil}
}
