package worker

import (
	"context"
	"errors"
	"net/http"
	"strings"
	"testing"
	"time"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
)

func TestQualifiedJSONLDHTTPRequiresFrozenConfigAndExactOriginPath(t *testing.T) {
	if len(qualifiedJSONLDHTTPDetails) != 5 {
		t.Fatal("five qualified configurations required")
	}
	for fingerprint, route := range qualifiedJSONLDHTTPDetails {
		original := queue.WorkdayDetailProfile{Profile: "jsonld.rendered-detail/v1", EffectiveBoardSHA256: fingerprint, SourceURL: "https://" + route.host + route.pathPrefix + "example", JSONLDConfig: map[string]any{"render": true, "timeout": float64(60000), "defaults": map[string]any{"language": "en"}}}
		if !qualifiedJSONLDHTTPDetail(original) {
			t.Fatal("qualified source refused")
		}
		for _, change := range []func(*queue.WorkdayDetailProfile){
			func(p *queue.WorkdayDetailProfile) { p.EffectiveBoardSHA256 = strings.Repeat("0", 64) },
			func(p *queue.WorkdayDetailProfile) { p.Profile = "jsonld.direct-detail/v1" },
			func(p *queue.WorkdayDetailProfile) {
				p.SourceURL = "http://" + route.host + route.pathPrefix + "example"
			},
			func(p *queue.WorkdayDetailProfile) {
				p.SourceURL = "https://foreign.example" + route.pathPrefix + "example"
			},
			func(p *queue.WorkdayDetailProfile) {
				p.SourceURL = "https://" + route.host + ".foreign.example" + route.pathPrefix + "example"
			},
			func(p *queue.WorkdayDetailProfile) {
				p.SourceURL = "https://user:secret@" + route.host + route.pathPrefix + "example"
			},
			func(p *queue.WorkdayDetailProfile) {
				p.SourceURL = "https://" + route.host + ":444" + route.pathPrefix + "example"
			},
			func(p *queue.WorkdayDetailProfile) { p.SourceURL = "https://" + route.host + "/unqualified/example" },
			func(p *queue.WorkdayDetailProfile) {
				p.SourceURL = "https://" + route.host + route.pathPrefix + "../unqualified"
			},
			func(p *queue.WorkdayDetailProfile) {
				p.SourceURL = "https://" + route.host + route.pathPrefix + "%2e%2e/unqualified"
			},
			func(p *queue.WorkdayDetailProfile) {
				p.SourceURL = "https://" + route.host + route.pathPrefix + "example#unqualified"
			},
			func(p *queue.WorkdayDetailProfile) { p.SkipSSL = true },
		} {
			changed := original
			change(&changed)
			if qualifiedJSONLDHTTPDetail(changed) {
				t.Fatal("unproved source or configuration substituted HTTP")
			}
		}
		fetch := jsonldHTTPDetailProfile(original)
		if fetch.Profile != "jsonld.direct-detail/v1" || fetch.SourceURL != original.SourceURL || fetch.EffectiveBoardSHA256 != fingerprint || fetch.JSONLDConfig["defaults"] == nil || fetch.JSONLDConfig["render"] != nil || fetch.JSONLDConfig["timeout"] != nil || original.JSONLDConfig["render"] != true || original.JSONLDConfig["timeout"] == nil {
			t.Fatal("detached HTTP parser view changed original authority or parser defaults")
		}
	}
}

func TestRealQualifiedJSONLDHTTPKeepsBrowserAuthorityPolicyAndSettlement(t *testing.T) {
	for _, mode := range []string{"success", "header-reserved", "meta-reserved", "unqualified"} {
		t.Run(mode, func(t *testing.T) {
			ctx := context.Background()
			f, authority, claim := independentDetailOwnedFixture(t, `{"scraper_type":"json-ld","scraper_config":{"render":true,"wait":"load","timeout":60000}}`, "", queue.Browser)
			detail, err := authority.ReadWorkdayDetail(ctx, claim)
			if err != nil {
				t.Fatal(err)
			}
			if mode != "unqualified" {
				fingerprint := detail.Profile().EffectiveBoardSHA256
				// Serial fixture only: use the same frozen-config guard with this
				// real private board, without granting any production identity.
				qualifiedJSONLDHTTPDetails[fingerprint] = jsonldHTTPDetailRoute{host: "example.com", pathPrefix: "/job/"}
				t.Cleanup(func() { delete(qualifiedJSONLDHTTPDetails, fingerprint) })
			}
			origins, renders := 0, 0
			client := richPipelineHTTP(t, func(w http.ResponseWriter, r *http.Request) {
				origins++
				if r.URL.Path != "/job/"+f.original || r.Method != "GET" {
					t.Error("substitution changed canonical origin request")
				}
				body := nativeJSONLDHTML
				if mode == "header-reserved" {
					w.Header().Set("TDM-Reservation", "1")
				}
				if mode == "meta-reserved" {
					body = strings.Replace(body, "<head>", `<head><meta name="tdm-reservation" content="1">`, 1)
				}
				w.Header().Set("Content-Type", "text/html; charset=utf-8")
				_, _ = w.Write([]byte(body))
			})
			renderer := heldRenderedDetail(func(ctx context.Context, profile queue.WorkdayDetailProfile) (map[string]any, *policy.Reservation, error) {
				renders++
				if mode != "unqualified" {
					return nil, nil, errors.New("qualified source unexpectedly rendered")
				}
				return parseHeldDetail(ctx, profile, heldRenderedResult(nativeJSONLDHTML, profile.SourceURL, 200))
			})
			circuits, err := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
			if err != nil {
				t.Fatal(err)
			}
			result, err := RunDetail(ctx, authority, claim, client, richPipelinePreparer(t, f).Processor, circuits, renderer)
			status := "succeeded"
			reserved := strings.HasSuffix(mode, "reserved")
			if reserved {
				status = "publisher_reserved"
			}
			if err != nil || result == nil || !result.Settled || result.Cycle == nil || result.Cycle.Status != status || mode != "unqualified" && (origins != 1 || renders != 0) || mode == "unqualified" && (origins != 0 || renders != 1) {
				t.Fatal("qualified HTTP or retained rendered route failed", err, origins, renders)
			}
			var title string
			var due time.Time
			var sqlReserved bool
			var failures, leases, descriptions int
			if err := f.pg.QueryRow(ctx, `SELECT coalesce(titles[1],''),next_scrape_at,tdm_reserved,scrape_failures,CASE WHEN leased_until IS NULL THEN 0 ELSE 1 END,(SELECT count(*) FROM descriptions WHERE posting_id=p.id) FROM job_posting p WHERE id=$1::uuid`, f.original).Scan(&title, &due, &sqlReserved, &failures, &leases, &descriptions); err != nil {
				t.Fatal(err)
			}
			if sqlReserved != reserved || failures != 0 || leases != 0 || reserved && descriptions != 0 || !reserved && (title != "Senior Software Engineer" || descriptions != 1) {
				t.Fatal("HTTP substitution changed canonical content or publisher/failure semantics")
			}
			if score, err := f.r.ZScore(ctx, "scrapes_browser:example.com", f.original).Result(); err != nil || score != float64(due.UnixMicro())/1e6 || f.r.ZCard(ctx, "inflight:browser").Val() != 0 || f.r.ZCard(ctx, "scrapes_simple:example.com").Val() != 0 {
				t.Fatal("substitution changed original browser queue, deadline or lease")
			}
		})
	}
}
