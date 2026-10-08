package apisniffer

import (
	"context"
	"encoding/json"
	"math/big"
	"net/http"
	"regexp"
	"strings"
	"sync"
)

const JarviOffersURL = "https://functions.prod.jarvi.tech/v1/public-api/rest/v2/offers?limit=50000"

var job51CTMID = regexp.MustCompile(`\bctmid\s*:\s*['"]?([0-9]{1,12})`)

func DiscoverJarvi(ctx context.Context, board, publicKey, currency string, fetch SmallProviderFetch) ([]map[string]any, bool, error) {
	if ctx == nil || fetch == nil {
		return nil, false, ErrOptions
	}
	if ctx.Err() != nil {
		return nil, false, ctx.Err()
	}
	if publicKey == "" {
		body, e := fetch(ctx, Request{Method: "GET", URL: board})
		if e != nil {
			return nil, false, e
		}
		embed, e := JarviEmbed(string(body))
		if e != nil {
			return nil, false, e
		}
		publicKey = embed["public_api_key"]
		if v := embed["currency"]; v != "" {
			currency = v
		}
	}
	if len(publicKey) > 4096 || strings.ContainsAny(publicKey, "\r\n\x00") {
		return nil, false, ErrOptions
	}
	body, e := fetch(ctx, Request{Method: "GET", URL: JarviOffersURL, Headers: http.Header{"X-Api-Key": []string{publicKey}}})
	if e != nil {
		return nil, false, e
	}
	if len(body) > 64<<20 {
		return nil, false, ErrInventory
	}
	d, e := Decode(body)
	if e != nil {
		return nil, false, e
	}
	root, ok := d.Value.(map[string]any)
	if !ok {
		return nil, false, ErrInventory
	}
	values, ok := root["data"].([]any)
	if !ok {
		return nil, false, ErrInventory
	}
	jobs := []map[string]any{}
	offers := 0
	for _, v := range values {
		if ctx.Err() != nil {
			return nil, false, ctx.Err()
		}
		row, ok := v.(map[string]any)
		if !ok {
			continue
		}
		offers++
		job, e := JarviJobFields(row, board, currency)
		if e != nil {
			return nil, false, e
		}
		if job != nil {
			jobs = append(jobs, job)
		}
	}
	truncated := offers >= 50000
	switch total := root["total"].(type) {
	case json.Number:
		if n, ok := new(big.Int).SetString(string(total), 10); ok {
			truncated = truncated || n.Cmp(big.NewInt(int64(offers))) > 0
		}
	case bool:
		truncated = truncated || total && offers == 0
	}
	if ctx.Err() != nil {
		return nil, false, ctx.Err()
	}
	return jobs, truncated, nil
}

func DiscoverJob51(ctx context.Context, board string, ctmid int64, fetch SmallProviderFetch, normalize SmallDescriptionNormalizer) ([]map[string]any, bool, error) {
	if ctx == nil || fetch == nil || normalize == nil {
		return nil, false, ErrOptions
	}
	if ctx.Err() != nil {
		return nil, false, ctx.Err()
	}
	if _, e := Job51BoardOrigin(board); e != nil {
		return nil, false, e
	}
	if ctmid == 0 {
		body, e := fetch(ctx, Request{Method: "GET", URL: board})
		if e != nil {
			return nil, false, e
		}
		if len(body) > 1_000_000 {
			return nil, false, ErrInventory
		}
		m := job51CTMID.FindStringSubmatch(string(body))
		if m == nil {
			return nil, false, ErrInventory
		}
		ctmid, e = job51Int(m[1])
		if e != nil {
			return nil, false, e
		}
	}
	request, e := Job51ListRequest(ctmid, 1)
	if e != nil {
		return nil, false, e
	}
	body, e := fetch(ctx, request)
	if e != nil {
		return nil, false, e
	}
	first, e := Job51JSONP(body)
	if e != nil {
		return nil, false, e
	}
	total, rows, e := Job51ListPage(first, 1)
	if e != nil {
		return nil, false, e
	}
	target := total
	if target > 50000 {
		target = 50000
	}
	pages := (target + 19) / 20
	for page := 2; page <= int(pages); page++ {
		request, e = Job51ListRequest(ctmid, page)
		if e != nil {
			return nil, false, e
		}
		body, e = fetch(ctx, request)
		if e != nil {
			return nil, false, e
		}
		parsed, e := Job51JSONP(body)
		if e != nil {
			return nil, false, e
		}
		count, next, e := Job51ListPage(parsed, page)
		if e != nil || count != total {
			return nil, false, ErrInventory
		}
		rows = append(rows, next...)
	}
	if int64(len(rows)) != target {
		return nil, false, ErrInventory
	}
	ids := []string{}
	seen := map[string]bool{}
	for _, row := range rows {
		id := smallText(row["jobid"])
		if !job51Identity.MatchString(id) || seen[id] {
			return nil, false, ErrInventory
		}
		seen[id] = true
		ids = append(ids, id)
	}
	jobs := make([]map[string]any, len(ids))
	work := make(chan int)
	failures := make(chan error, 1)
	held, cancel := context.WithCancel(ctx)
	defer cancel()
	var workers sync.WaitGroup
	for worker := 0; worker < 5; worker++ {
		workers.Add(1)
		go func() {
			defer workers.Done()
			for {
				select {
				case <-held.Done():
					return
				case index, open := <-work:
					if !open {
						return
					}
					if held.Err() != nil {
						return
					}
					request, e := Job51DetailRequest(ids[index])
					var body []byte
					var raw map[string]any
					if e == nil {
						body, e = fetch(held, request)
					}
					if e == nil {
						raw, e = Job51JSONP(body)
					}
					if e == nil {
						jobs[index], e = Job51JobFields(raw, ctmid, ids[index], normalize)
					}
					if e != nil {
						select {
						case failures <- e:
						default:
						}
						cancel()
						return
					}
				}
			}
		}()
	}
send:
	for index := range ids {
		select {
		case <-held.Done():
			break send
		case work <- index:
		}
	}
	close(work)
	workers.Wait()
	select {
	case e := <-failures:
		return nil, false, e
	default:
	}
	if ctx.Err() != nil {
		return nil, false, ctx.Err()
	}
	return jobs, total > 50000, nil
}
