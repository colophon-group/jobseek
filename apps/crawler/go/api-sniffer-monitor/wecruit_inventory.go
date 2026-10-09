package apisniffer

import (
	"context"
	"errors"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"sync"
	"time"
)

var wecruitSnapshotChanged = errors.New("Wecruit inventory changed during pagination")

func wecruitEnvelope(d *Document) (map[string]any, error) {
	root, ok := d.Value.(map[string]any)
	if !ok {
		return nil, ErrInventory
	}
	state, err := d.String(root["state"])
	data, ok := root["data"].(map[string]any)
	if err != nil || state != "200" || !ok {
		return nil, ErrInventory
	}
	return data, nil
}

func DiscoverWecruit(ctx context.Context, origin, suite string, lanes []int, fetch SmallProviderFetch) ([]map[string]any, bool, error) {
	u, err := url.Parse(origin)
	if ctx == nil || fetch == nil || err != nil || u.Scheme != "https" || u.User != nil || u.Hostname() == "" || u.Path != "" || u.RawQuery != "" || u.Fragment != "" || u.Port() != "" && u.Port() != "443" || !regexpWecruitSuite(suite) {
		return nil, false, ErrOptions
	}
	suite = strings.ToLower(suite)
	if lanes == nil {
		lanes = []int{1, 2, 12, 13}
	}
	seenLanes := map[int]bool{}
	if len(lanes) == 0 {
		return nil, false, ErrOptions
	}
	for _, lane := range lanes {
		if lane != 1 && lane != 2 && lane != 12 && lane != 13 || seenLanes[lane] {
			return nil, false, ErrOptions
		}
		seenLanes[lane] = true
	}
	post := func(ctx context.Context, endpoint string, form url.Values) (*Document, map[string]any, error) {
		if err := ctx.Err(); err != nil {
			return nil, nil, err
		}
		body, err := fetch(ctx, Request{Method: "POST", URL: endpoint, Body: form.Encode(), Headers: http.Header{"Content-Type": []string{"application/x-www-form-urlencoded"}}})
		if err != nil {
			return nil, nil, err
		}
		if len(body) > 10_000_000 {
			return nil, nil, ErrInventory
		}
		d, err := Decode(body)
		if err != nil {
			return nil, nil, ErrInventory
		}
		data, err := wecruitEnvelope(d)
		return d, data, err
	}
	listingURL := origin + "/wecruit/positionInfo/listPosition/SU" + suite
	page := func(lane, number, requestedSize int) ([]map[string]any, int, int, int, error) {
		_, data, err := post(ctx, listingURL, url.Values{"isFrompb": {"true"}, "recruitType": {strconv.Itoa(lane)}, "pageSize": {strconv.Itoa(requestedSize)}, "currentPage": {strconv.Itoa(number)}})
		if err != nil {
			return nil, 0, 0, 0, err
		}
		form, ok := data["pageForm"].(map[string]any)
		if !ok {
			return nil, 0, 0, 0, ErrInventory
		}
		values, ok := form["pageData"].([]any)
		if !ok {
			return nil, 0, 0, 0, ErrInventory
		}
		if len(values) == 0 && jobCloudNumberEquals(form["totalPage"], 0) && jobCloudNumberEquals(form["dataCount"], 0) && jobCloudNumberEquals(data["positonNum"], 0) &&
			(jobCloudNumberEquals(form["pageSize"], 0) && jobCloudNumberEquals(form["currentPage"], 0) || jobCloudNumberEquals(form["pageSize"], 50) && jobCloudNumberEquals(form["currentPage"], number)) {
			return []map[string]any{}, 0, 0, 50, nil
		}
		pages, e1 := smallInt(form["totalPage"], false)
		size, e2 := smallInt(form["pageSize"], false)
		current, e3 := smallInt(form["currentPage"], false)
		total, e4 := smallInt(form["dataCount"], false)
		providerTotal, e5 := smallInt(data["positonNum"], false)
		if e1 != nil || e2 != nil || e3 != nil || e4 != nil || e5 != nil || pages < 1 || size < 1 || current != number || providerTotal != total || len(values) > size || pages != (total+size-1)/size {
			return nil, 0, 0, 0, ErrInventory
		}
		rows := []map[string]any{}
		for _, value := range values {
			row, ok := value.(map[string]any)
			if !ok {
				return nil, 0, 0, 0, ErrInventory
			}
			rows = append(rows, row)
		}
		return rows, total, pages, size, nil
	}
	collect := func() ([]map[string]any, bool, error) {
		listings := []map[string]any{}
		seen := map[string]bool{}
		truncated := false
		for _, lane := range lanes {
			rows, total, pages, size, err := page(lane, 1, 50)
			if err != nil {
				return nil, false, err
			}
			for number := 2; number <= pages; number++ {
				tail, observedTotal, observedPages, observedSize, err := page(lane, number, size)
				if err != nil {
					return nil, false, err
				}
				if observedTotal != total || observedPages != pages || observedSize != size {
					return nil, false, wecruitSnapshotChanged
				}
				rows = append(rows, tail...)
			}
			if len(rows) != total {
				return nil, false, ErrInventory
			}
			for _, row := range rows {
				id, ok := row["postId"].(string)
				if !ok || !regexpWecruitSuite(id) || seen[id] || !jobCloudNumberEquals(row["recruitType"], lane) {
					return nil, false, ErrInventory
				}
				providerSuite, err := (&Document{}).String(row["currentSuiteKey"])
				if err != nil || strings.ToLower(providerSuite) != suite {
					return nil, false, ErrInventory
				}
				seen[id] = true
				if len(listings) >= 50000 {
					truncated = true
				} else {
					listings = append(listings, row)
				}
			}
		}
		return listings, truncated, nil
	}
	listings, truncated, err := collect()
	if errors.Is(err, wecruitSnapshotChanged) {
		timer := time.NewTimer(time.Second)
		defer timer.Stop()
		select {
		case <-ctx.Done():
			return nil, false, ctx.Err()
		case <-timer.C:
		}
		listings, truncated, err = collect()
	}
	if err != nil {
		return nil, false, err
	}
	child, cancel := context.WithCancel(ctx)
	defer cancel()
	jobs := make([]map[string]any, len(listings))
	var first error
	var mu sync.Mutex
	var wg sync.WaitGroup
	sem := make(chan struct{}, 20)
	for index, row := range listings {
		wg.Add(1)
		go func(index int, row map[string]any) {
			defer wg.Done()
			select {
			case sem <- struct{}{}:
				defer func() { <-sem }()
			case <-child.Done():
				return
			}
			d, detail, err := post(child, origin+"/wecruit/positionInfo/listPositionDetail/SU"+suite, url.Values{"postId": {row["postId"].(string)}})
			if err == nil {
				if detail["postId"] != row["postId"] || !wecruitLaneEqual(detail["recruitType"], row["recruitType"]) {
					err = ErrInventory
				} else {
					jobs[index], err = WecruitJobFields(d, origin, suite, row, detail)
				}
			}
			if err != nil {
				mu.Lock()
				if first == nil {
					first = err
				}
				mu.Unlock()
				cancel()
			}
		}(index, row)
	}
	wg.Wait()
	if first != nil {
		return nil, false, first
	}
	if err := ctx.Err(); err != nil {
		return nil, false, err
	}
	return jobs, truncated, nil
}

func regexpWecruitSuite(value string) bool {
	if len(value) != 24 {
		return false
	}
	for _, c := range value {
		if !(c >= '0' && c <= '9' || c >= 'a' && c <= 'f' || c >= 'A' && c <= 'F') {
			return false
		}
	}
	return true
}

func wecruitLaneEqual(a, b any) bool {
	for _, lane := range []int{1, 2, 12, 13} {
		if jobCloudNumberEquals(a, lane) && jobCloudNumberEquals(b, lane) {
			return true
		}
	}
	// The listing validator already constrains every admitted lane.
	return false
}
