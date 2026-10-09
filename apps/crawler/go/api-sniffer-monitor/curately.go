package apisniffer

import (
	"context"
	"encoding/json"
	"net/http"
	"strconv"
	"strings"
	"time"
)

func curatelyEnvelope(body []byte) (map[string]any, error) {
	d, e := Decode(body)
	if e != nil {
		return nil, ErrInventory
	}
	m, ok := d.Value.(map[string]any)
	if !ok || m["Success"] != true {
		return nil, ErrInventory
	}
	status, ok := m["Status"].(json.Number)
	if !ok {
		return nil, ErrInventory
	}
	n, e := status.Float64()
	if e != nil || n != 200 {
		return nil, ErrInventory
	}
	return m, nil
}

func CuratelySearchRequest(clientID, offset, days int) Request {
	body, _ := json.Marshal(map[string]any{"query": "", "city": "", "state": "", "zipcode": "", "radius": "30", "daysback": strconv.Itoa(days), "jobHours": "", "isRemote": false, "jobType": "", "clientids": strconv.Itoa(clientID), "next": offset, "type": ""})
	return Request{Method: "POST", URL: CuratelySearchURL, Body: string(body), Headers: http.Header{"Content-Type": []string{"application/json"}}}
}

type curatelySnapshotChange struct{ total int }

func (*curatelySnapshotChange) Error() string { return "curately snapshot changed" }

func DiscoverCurately(ctx context.Context, o FinalHTTPProviderOptions, fetch SmallProviderFetch, wait func(context.Context, time.Duration) error) ([]map[string]any, bool, error) {
	if ctx == nil || fetch == nil || wait == nil || o.Provider != "curately" || o.SnapshotAttempts < 1 || o.SemanticZeroAttempts < 1 {
		return nil, false, ErrOptions
	}
	client := o.ClientID
	if client == 0 {
		b, e := fetch(ctx, Request{Method: "GET", URL: CuratelyAPIBase + "/getByShortName/" + o.Tenant})
		if e != nil {
			return nil, false, e
		}
		m, e := curatelyEnvelope(b)
		if e != nil {
			return nil, false, e
		}
		tenant, ok := m["shortName"].(string)
		client, e = smallInt(m["clientId"], true)
		if !ok || strings.ToLower(tenant) != o.Tenant || e != nil || client < 1 {
			return nil, false, ErrInventory
		}
	}
	prior := -1
	for attempt := 0; attempt < o.SnapshotAttempts; attempt++ {
		jobs, truncated, e := curatelySnapshot(ctx, o, client, prior, fetch, wait)
		if e == nil {
			return jobs, truncated, nil
		}
		change, ok := e.(*curatelySnapshotChange)
		if !ok || attempt+1 == o.SnapshotAttempts {
			return nil, false, e
		}
		if change.total > 0 {
			prior = change.total
		}
		if e = wait(ctx, time.Duration(attempt+1)*time.Second); e != nil {
			return nil, false, e
		}
	}
	return nil, false, ErrInventory
}

func curatelySnapshot(ctx context.Context, o FinalHTTPProviderOptions, client, prior int, fetch SmallProviderFetch, wait func(context.Context, time.Duration) error) ([]map[string]any, bool, error) {
	jobs := []map[string]any{}
	seen := map[int]bool{}
	offset, total := 0, -1
	for {
		var rows []any
		var observed int
		for attempt := 0; attempt < o.SemanticZeroAttempts; attempt++ {
			if e := ctx.Err(); e != nil {
				return nil, false, e
			}
			b, e := fetch(ctx, CuratelySearchRequest(client, offset, o.DaysBack))
			if e != nil {
				return nil, false, e
			}
			m, e := curatelyEnvelope(b)
			if e != nil {
				return nil, false, e
			}
			var ok bool
			rows, ok = m["List"].([]any)
			if !ok {
				return nil, false, ErrInventory
			}
			observed, e = smallInt(m["TotalSize"], false)
			if e != nil {
				return nil, false, ErrInventory
			}
			if !(total > 0 && observed == 0) || attempt+1 == o.SemanticZeroAttempts {
				break
			}
			if e = wait(ctx, time.Duration(attempt+1)*time.Second); e != nil {
				return nil, false, e
			}
		}
		if total == -1 {
			if observed == 0 && prior > 0 {
				return nil, false, &curatelySnapshotChange{prior}
			}
			total = observed
		} else if observed != total {
			return nil, false, &curatelySnapshotChange{total}
		}
		if len(rows) == 0 {
			if offset < min(total, 50000) {
				return nil, false, &curatelySnapshotChange{total}
			}
			break
		}
		for _, raw := range rows {
			row, ok := raw.(map[string]any)
			if !ok {
				return nil, false, ErrInventory
			}
			id, e := smallInt(row["jobId"], true)
			owner, ownerErr := smallInt(row["clientId"], true)
			if e != nil || id < 1 || ownerErr != nil || owner != client {
				return nil, false, ErrInventory
			}
			if seen[id] {
				return nil, false, &curatelySnapshotChange{total}
			}
			seen[id] = true
			field, e := CuratelyJobFields(row, o.Tenant, o.Currency, o.SalaryUnit, o.Language)
			if e != nil {
				return nil, false, e
			}
			if field != nil {
				jobs = append(jobs, field)
			}
		}
		offset += len(rows)
		if offset >= min(total, 50000) {
			break
		}
	}
	if total <= 50000 && offset != total {
		return nil, false, &curatelySnapshotChange{total}
	}
	if total > 50000 {
		return jobs[:min(len(jobs), 50000)], true, nil
	}
	return jobs, false, nil
}
