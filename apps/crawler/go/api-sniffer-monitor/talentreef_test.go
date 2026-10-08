package apisniffer

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"reflect"
	"testing"
)

func TestTalentReefMatchesActualPythonScopePayloadCountersAndRichFields(t *testing.T) {
	body, err := os.ReadFile("testdata/python_talentreef.json")
	if err != nil {
		t.Fatal(err)
	}
	d, err := Decode(body)
	if err != nil {
		t.Fatal(err)
	}
	corpus := d.Value.(map[string]any)
	for _, raw := range corpus["scopes"].([]any) {
		c := raw.(map[string]any)
		body, _ := json.Marshal(c["payload"])
		doc, _ := Decode(body)
		s, err := ParseTalentReefScope(doc, TenthProviderOptions{Alias: "sample", Locale: c["locale"].(string)})
		if c["error"] != nil {
			if err == nil {
				t.Fatal("unproved brand scope accepted", c["name"])
			}
			continue
		}
		want := c["value"].(map[string]any)
		got := map[string]any{"alias": s.Alias, "client_id": s.ClientID, "locale": s.Locale, "brands": s.Brands}
		b, _ := json.Marshal(got)
		dd, _ := Decode(b)
		if err != nil || !reflect.DeepEqual(dd.Value, want) {
			t.Fatal("brand scope differs", c["name"], got, want, err)
		}
	}
	s := TalentReefScope{Alias: "sample", ClientID: "123", Locale: "en", Brands: []string{"One", "Two"}}
	req, err := TalentReefSearchRequest(s, 1000)
	request, _ := Decode([]byte(req.Body))
	if err != nil || !reflect.DeepEqual(request.Value, corpus["request"]) {
		t.Fatal("search payload lost client/brands/external boundary", err)
	}
	for _, raw := range corpus["pages"].([]any) {
		c := raw.(map[string]any)
		b, _ := json.Marshal(c["payload"])
		doc, _ := Decode(b)
		rows, total, err := ParseTalentReefPage(doc)
		if c["error"] != nil {
			if err == nil {
				t.Fatal("bad total accepted", c["name"])
			}
			continue
		}
		n, _ := tenthNonnegative(c["total"])
		if b, ok := c["total"].(bool); ok && b {
			n = 1
		}
		b, _ = json.Marshal(rows)
		dd, _ := Decode(b)
		if err != nil || total != n || !reflect.DeepEqual(dd.Value, c["rows"]) {
			t.Fatal("page parity differs", c["name"], err)
		}
	}
	for _, raw := range corpus["jobs"].([]any) {
		c := raw.(map[string]any)
		got, err := TalentReefJobFields(d, c["hit"].(map[string]any), s)
		if c["value"] == nil {
			if err == nil {
				t.Fatal("invalid job accepted", c["name"])
			}
			continue
		}
		want := c["value"].(map[string]any)
		for key, value := range want {
			if value == nil {
				delete(want, key)
			}
		}
		b, _ := json.Marshal(got)
		dd, _ := Decode(b)
		if err != nil || !reflect.DeepEqual(dd.Value, want) {
			t.Fatal("rich fields differ", c["name"], dd.Value, want, err)
		}
	}
}

func TestTalentReefCompleteSearchRejectsGapsAndMarksInvalidOrDuplicateInventory(t *testing.T) {
	for _, mode := range []string{"complete", "gap", "duplicate", "invalid", "all-invalid", "empty"} {
		t.Run(mode, func(t *testing.T) {
			o, _ := TenthProviderOptionsFromMetadata("talentreef", "https://apply.jobappnetwork.com/sample", "{}")
			calls := 0
			jobs, truncated, err := DiscoverTalentReef(context.Background(), o, func(ctx context.Context, r Request) ([]byte, error) {
				if !o.ResourceMatches(r.URL) {
					t.Fatal("unbound resource")
				}
				if r.Method == "GET" {
					return []byte(`[{"published":true,"clientId":"123","locale":"en","brands":["One"]}]`), nil
				}
				var p map[string]any
				if json.Unmarshal([]byte(r.Body), &p) != nil {
					t.Fatal("invalid request")
				}
				if int(p["from"].(float64)) != calls*1000 {
					t.Fatal("offset did not preserve raw hits")
				}
				calls++
				total := 1001
				rows := []any{}
				if mode == "empty" {
					total = 0
				} else if calls == 1 {
					for n := 0; n < 1000; n++ {
						id := fmt.Sprint(n)
						title := "Engineer"
						if mode == "all-invalid" {
							title = ""
						}
						rows = append(rows, map[string]any{"_source": map[string]any{"jobId": id, "positionType": title, "description": "<p>Build Go.</p>"}})
					}
				} else if mode != "gap" {
					id := "1000"
					title := "Engineer"
					if mode == "duplicate" {
						id = "0"
					}
					if mode == "invalid" || mode == "all-invalid" {
						title = ""
					}
					rows = append(rows, map[string]any{"_source": map[string]any{"jobId": id, "positionType": title}})
				}
				return json.Marshal(map[string]any{"hits": map[string]any{"total": total, "hits": rows}})
			})
			if mode == "gap" || mode == "all-invalid" {
				if err == nil {
					t.Fatal("partial invalid result accepted")
				}
				return
			}
			want := 1001
			if mode == "duplicate" || mode == "invalid" {
				want = 1000
			}
			if mode == "empty" {
				want = 0
			}
			if err != nil || len(jobs) != want || truncated != (mode == "duplicate" || mode == "invalid") {
				t.Fatal("complete inventory contract differs", len(jobs), truncated, err)
			}
		})
	}
}

func TestTalentReefMalformedMetadataCannotBecomeCompleteInventory(t *testing.T) {
	o, _ := TenthProviderOptionsFromMetadata("talentreef", "https://apply.jobappnetwork.com/sample", "{}")
	jobs, truncated, err := DiscoverTalentReef(context.Background(), o, func(ctx context.Context, r Request) ([]byte, error) {
		if r.Method == "GET" {
			return []byte(`[{"published":true,"clientId":"123","locale":"en","brands":["One"]}]`), nil
		}
		return []byte(`{"hits":{"total":1,"hits":[{"_source":{"jobId":17,"positionType":"Engineer","brand":{"name":"One"}}}]}}`), nil
	})
	if err == nil || len(jobs) != 0 || truncated {
		t.Fatal("malformed metadata became an authoritative inventory")
	}
}
