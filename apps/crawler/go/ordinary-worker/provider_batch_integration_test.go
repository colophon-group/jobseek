package worker

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"reflect"
	"strings"
	"sync"
	"testing"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
)

type providerBatchHTTPCase struct {
	Name           string
	BoardURL       string `json:"board_url"`
	Metadata       json.RawMessage
	Pages          map[string]json.RawMessage
	NativeReserved bool `json:"native_reserved"`
	Expected       struct {
		Error          any
		Reserved, Gone bool
		Jobs           []map[string]any
		URLs           []string
		Rich           map[string]map[string]any
		Patch          map[string]any
	}
}

func providerBatchReference(t *testing.T, provider, name string) providerBatchHTTPCase {
	t.Helper()
	body, e := os.ReadFile("testdata/python_" + provider + ".json")
	if e != nil {
		t.Fatal(e)
	}
	var corpus struct{ HTTP, Monitor []providerBatchHTTPCase }
	if json.Unmarshal(body, &corpus) != nil {
		t.Fatal("invalid provider reference")
	}
	rows := corpus.HTTP
	if provider == "eightfold" {
		rows = corpus.Monitor
	}
	for _, row := range rows {
		if row.Name == name {
			return row
		}
	}
	t.Fatal("provider reference case missing", provider, name)
	return providerBatchHTTPCase{}
}

func providerBatchReferenceHTTP(t *testing.T, c providerBatchHTTPCase) *VerifiedDirectHTTP {
	t.Helper()
	counts := map[string]int{}
	var mutex sync.Mutex
	return richPipelineHTTP(t, func(w http.ResponseWriter, r *http.Request) {
		key := "https://" + r.Host + r.URL.String()
		if key == api.AlmaGraphQLURL {
			var body struct {
				Query     string
				Variables map[string]any
			}
			d := json.NewDecoder(r.Body)
			d.UseNumber()
			if d.Decode(&body) != nil {
				t.Error("invalid GraphQL request")
				w.WriteHeader(500)
				return
			}
			if strings.Contains(body.Query, "jobAdList") {
				key = "LIST:" + fmt.Sprint(body.Variables["page"])
			} else {
				key = "DETAIL:" + fmt.Sprint(body.Variables["jobId"])
			}
		}
		raw, ok := c.Pages[key]
		if !ok {
			t.Error("request left declared reference", key)
			w.WriteHeader(500)
			return
		}
		mutex.Lock()
		index := counts[key]
		counts[key]++
		mutex.Unlock()
		if len(raw) > 0 && raw[0] == '[' {
			var rows []json.RawMessage
			if json.Unmarshal(raw, &rows) != nil || len(rows) == 0 {
				t.Error("invalid sequence")
				w.WriteHeader(500)
				return
			}
			raw = rows[min(index, len(rows)-1)]
		}
		var response struct {
			Body    string
			Status  int
			Headers map[string]string
		}
		if json.Unmarshal(raw, &response) != nil {
			t.Error("invalid fixture response")
			w.WriteHeader(500)
			return
		}
		for key, value := range response.Headers {
			w.Header().Set(key, value)
		}
		w.WriteHeader(response.Status)
		fmt.Fprint(w, response.Body)
	})
}

func TestRealProviderBatchReferenceHTTPCommitsCanonicalContentAndDetails(t *testing.T) {
	realProviderBatchTransportCases(t, false)
}
func TestRealProxyEightfoldReferenceHTTPCommitsCanonicalContentAndDetails(t *testing.T) {
	realProviderBatchTransportCases(t, true)
}
func realProviderBatchTransportCases(t *testing.T, proxy bool) {
	cases := map[string][]string{
		"mokahr":     {"complete", "enrich-new", "enrich-retained", "zero-confirmed", "foreign-row", "page-gone", "page-shutdown", "partition-failure-discards-prefix"},
		"almacareer": {"complete", "empty", "later-listing-failure", "detail-failure-teaser", "listing-header-reserved", "detail-header-reserved", "script-native-header-boundary", "script-gone"},
		"eightfold":  {"first-full", "incremental", "cached-disabled", "probe-disabled", "manual-first", "boundary-jitter-resets-safety", "probe-reserved", "fetch-reserved", "fetch-prefix-failure"},
	}
	for _, provider := range []string{"mokahr", "almacareer", "eightfold"} {
		if proxy && provider != "eightfold" {
			continue
		}
		for _, mode := range cases[provider] {
			t.Run(provider+"/"+mode, func(t *testing.T) {
				name := mode
				if strings.HasPrefix(mode, "enrich-") {
					name = "complete"
				}
				c := providerBatchReference(t, provider, name)
				metadata := map[string]any{}
				if json.Unmarshal(c.Metadata, &metadata) != nil {
					t.Fatal("invalid metadata fixture")
				}
				if provider == "almacareer" {
					metadata["scraper_type"] = "skip"
				}
				if provider == "eightfold" || strings.HasPrefix(mode, "enrich-") {
					metadata["scraper_type"] = provider
					metadata["scraper_config"] = map[string]any{"enrich": []string{"description"}}
				}
				if proxy {
					metadata["proxy"] = true
				}
				body, e := json.Marshal(metadata)
				if e != nil {
					t.Fatal(e)
				}
				f := privateRichPipelineFixtureURL(t, provider, string(body), c.BoardURL)
				ctx := context.Background()
				if mode == "enrich-retained" {
					source := c.Expected.Jobs[0]["url"].(string)
					if _, e := f.pg.Exec(ctx, "UPDATE job_posting SET source_url=$2,next_scrape_at=NULL,description_r2_hash=123 WHERE id=$1::uuid", f.original, source); e != nil {
						t.Fatal(e)
					}
					if _, e := f.pg.Exec(ctx, "INSERT INTO descriptions(posting_id,locale,html,hash,r2_uploaded) VALUES($1::uuid,'zh','<p>Retained authoritative body</p>',123,true),($1::uuid,'en','<p>Retained authoritative body</p>',123,true)", f.original); e != nil {
						t.Fatal(e)
					}
				}
				claim, circuits := claimFixture(t, f)
				client := providerBatchReferenceHTTP(t, c)
				if proxy {
					client = credentialedProxyFixture(t, client)
				}
				result, e := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits)
				if e != nil || result == nil || !result.Settled {
					t.Fatal("provider did not reach canonical settlement", result, e)
				}
				assertRichDeadlineAndLease(t, f, provider)
				var reserved bool
				var failures, gone int
				if e := f.pg.QueryRow(ctx, "SELECT tdm_reserved,consecutive_failures,gone_confirmation_count FROM job_board WHERE id=$1::uuid", f.board).Scan(&reserved, &failures, &gone); e != nil {
					t.Fatal(e)
				}
				wantReserved := c.NativeReserved || c.Expected.Reserved
				if wantReserved {
					if !reserved || result.Cycle.Status != "publisher_reserved" || result.Batches.Inserted != 0 {
						t.Fatal("publisher policy reached canonical content instead of reservation")
					}
					return
				}
				if reserved {
					t.Fatal("unexpected reservation")
				}
				if c.Expected.Gone {
					if gone != 1 || failures != 0 || result.Batches.Inserted != 0 {
						t.Fatal("provider disappearance changed failure/visibility authority")
					}
					return
				}
				if c.Expected.Error == true {
					if failures != 1 || result.Batches.Inserted != 0 {
						t.Fatal("failed prefix reached canonical inventory")
					}
					return
				}
				if failures != 0 || gone != 0 {
					t.Fatal("valid inventory recorded as failure/gone")
				}
				urls := c.Expected.URLs
				if provider != "eightfold" {
					for _, job := range c.Expected.Jobs {
						urls = append(urls, job["url"].(string))
					}
				}
				for _, source := range urls {
					var title *string
					var locales []string
					var due bool
					var id string
					if e := f.pg.QueryRow(ctx, "SELECT id::text,titles[1],locales,next_scrape_at IS NOT NULL FROM job_posting WHERE source_url=$1 AND board_id=$2::uuid", source, f.board).Scan(&id, &title, &locales, &due); e != nil {
						t.Fatal(e)
					}
					var reference map[string]any
					if provider == "eightfold" {
						reference = c.Expected.Rich[source]
					} else {
						for _, job := range c.Expected.Jobs {
							if job["url"] == source {
								reference = job
							}
						}
					}
					if reference != nil {
						if title == nil || *title != reference["title"] || len(locales) == 0 {
							t.Fatal("canonical title/locales lost reference fields")
						}
					} else if title != nil || len(locales) > 0 {
						t.Fatal("sitemap-only member invented rich content")
					}
					if provider == "eightfold" || mode == "enrich-new" {
						if !due || f.r.HGet(ctx, "scrape:"+id, "source_url").Val() != source {
							t.Fatal("mandatory detail did not enter canonical cache/queue")
						}
					}
					if provider == "almacareer" && due {
						t.Fatal("complete widget invented a detail scrape")
					}
				}
				if mode == "enrich-retained" {
					var overwritten int
					if e := f.pg.QueryRow(ctx, "SELECT count(*) FROM descriptions WHERE posting_id=$1::uuid AND locale IN ('en','zh') AND (html<>'<p>Retained authoritative body</p>' OR hash<>123 OR NOT r2_uploaded)", f.original).Scan(&overwritten); e != nil || overwritten != 0 {
						t.Fatal("monitor replaced delegated canonical description", e)
					}
				}
				if provider == "eightfold" {
					var canonical []byte
					if e := f.pg.QueryRow(ctx, "SELECT metadata->'pcsx_watermark' FROM job_board WHERE id=$1::uuid", f.board).Scan(&canonical); e != nil {
						t.Fatal(e)
					}
					var expected any
					if len(canonical) > 0 && json.Unmarshal(canonical, &expected) != nil {
						t.Fatal("invalid canonical watermark")
					}
					var cached map[string]any
					if json.Unmarshal([]byte(f.r.HGet(ctx, "board:"+f.board, "metadata").Val()), &cached) != nil || !reflect.DeepEqual(expected, cached["pcsx_watermark"]) {
						t.Fatal("terminal watermark not synchronized with exact cached board")
					}
					if mode == "fetch-prefix-failure" {
						var original map[string]any
						if json.Unmarshal(c.Metadata, &original) != nil || !reflect.DeepEqual(expected, original["pcsx_watermark"]) {
							t.Fatal("failed PCSX prefix advanced watermark")
						}
					}
				}
			})
		}
	}
}

func TestRealEightfoldHybridRefreshPreservesTouchedAndRelistedDetailContent(t *testing.T) {
	for _, mode := range []string{"touched", "relisted"} {
		t.Run(mode, func(t *testing.T) {
			c := providerBatchReference(t, "eightfold", "first-full")
			source := ""
			for url := range c.Expected.Rich {
				source = url
				break
			}
			if source == "" {
				t.Fatal("missing rich reference member")
			}
			var md map[string]any
			if err := json.Unmarshal(c.Metadata, &md); err != nil {
				t.Fatal(err)
			}
			md["scraper_type"] = "eightfold"
			md["scraper_config"] = map[string]any{"enrich": []string{"description"}}
			raw, err := json.Marshal(md)
			if err != nil {
				t.Fatal(err)
			}
			f := privateRichPipelineFixtureURL(t, "eightfold", string(raw), c.BoardURL)
			ctx := context.Background()
			if _, err := f.pg.Exec(ctx, "UPDATE job_posting SET source_url=$2,titles=ARRAY['Authoritative detail'],employment_type='part_time',location_ids=ARRAY[2],is_active=$3,description_r2_hash=123 WHERE id=$1::uuid", f.original, source, mode == "touched"); err != nil {
				t.Fatal(err)
			}
			if _, err := f.pg.Exec(ctx, "INSERT INTO descriptions(posting_id,locale,html,hash,r2_uploaded) VALUES($1::uuid,'en','<p>Authoritative detail body</p>',123,true)", f.original); err != nil {
				t.Fatal(err)
			}
			claim, circuits := claimFixture(t, f)
			result, err := RunGreenhouseClaim(ctx, f.a, claim, providerBatchReferenceHTTP(t, c), richPipelinePreparer(t, f), circuits)
			if err != nil || result == nil || !result.Settled {
				t.Fatal("hybrid refresh failed", err)
			}
			var titles []string
			var employment, html string
			var locations []int32
			var active, uploaded bool
			if err := f.pg.QueryRow(ctx, "SELECT p.titles,p.employment_type,p.location_ids,p.is_active,d.html,d.r2_uploaded FROM job_posting p JOIN descriptions d ON d.posting_id=p.id AND d.locale='en' WHERE p.id=$1::uuid", f.original).Scan(&titles, &employment, &locations, &active, &html, &uploaded); err != nil {
				t.Fatal(err)
			}
			if fmt.Sprint(titles) != "[Authoritative detail]" || employment != "part_time" || fmt.Sprint(locations) != "[2]" || !active || html != "<p>Authoritative detail body</p>" || !uploaded {
				t.Fatal("hybrid inventory replaced authoritative detail content")
			}
			if mode == "touched" && result.Batches.Touched < 1 || mode == "relisted" && result.Batches.Relisted < 1 {
				t.Fatal("existing member did not use expected lifecycle", result.Batches)
			}
			assertRichDeadlineAndLease(t, f, "eightfold")
		})
	}
}
