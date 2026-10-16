package worker

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
	"net/http"
	"strings"
	"testing"
	"time"
)

const adpDOMFixtureSource = "https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html?cid=fixture-client&ccId=fixture-center&jobId=123_1&lang=en_US"

func adpDOMFixtureScope() string {
	sum := sha256.Sum256([]byte("fixture-client\nfixture-center"))
	return hex.EncodeToString(sum[:])
}

func TestQualifiedADPDOMHTTPRequiresOriginalConfigClientAndOrigin(t *testing.T) {
	fp := strings.Repeat("a", 64)
	qualifiedADPDOMHTTPDetails[fp] = adpDOMFixtureScope()
	defer delete(qualifiedADPDOMHTTPDetails, fp)
	p := queue.WorkdayDetailProfile{Profile: "dom.rendered-detail/v1", EffectiveBoardSHA256: fp, SourceURL: adpDOMFixtureSource}
	if !qualifiedADPDOMHTTPDetail(p) {
		t.Fatal("qualified source refused")
	}
	for _, change := range []func(*queue.WorkdayDetailProfile){
		func(p *queue.WorkdayDetailProfile) { p.EffectiveBoardSHA256 = strings.Repeat("b", 64) },
		func(p *queue.WorkdayDetailProfile) { p.Profile = "dom.direct-detail/v1" },
		func(p *queue.WorkdayDetailProfile) { p.SkipSSL = true },
		func(p *queue.WorkdayDetailProfile) { p.SourceURL = strings.Replace(p.SourceURL, "https:", "http:", 1) },
		func(p *queue.WorkdayDetailProfile) {
			p.SourceURL = strings.Replace(p.SourceURL, "workforcenow.adp.com", "workforcenow.adp.com.foreign.example", 1)
		},
		func(p *queue.WorkdayDetailProfile) {
			p.SourceURL = strings.Replace(p.SourceURL, "https://", "https://user:secret@", 1)
		},
		func(p *queue.WorkdayDetailProfile) {
			p.SourceURL = strings.Replace(p.SourceURL, "adp.com/", "adp.com:444/", 1)
		},
		func(p *queue.WorkdayDetailProfile) {
			p.SourceURL = strings.Replace(p.SourceURL, "fixture-client", "foreign-client", 1)
		},
		func(p *queue.WorkdayDetailProfile) {
			p.SourceURL = strings.Replace(p.SourceURL, "fixture-center", "foreign-center", 1)
		},
		func(p *queue.WorkdayDetailProfile) { p.SourceURL += "&cid=fixture-client" },
		func(p *queue.WorkdayDetailProfile) { p.SourceURL += "#unproved" },
		func(p *queue.WorkdayDetailProfile) {
			p.SourceURL = strings.Replace(p.SourceURL, "recruitment.html", "../recruitment.html", 1)
		},
		func(p *queue.WorkdayDetailProfile) {
			p.SourceURL = strings.Replace(p.SourceURL, "recruitment.html", "%2e%2e/recruitment.html", 1)
		},
		func(p *queue.WorkdayDetailProfile) {
			p.SourceURL = strings.Replace(p.SourceURL, "jobId=123_1", "jobId=../foreign", 1)
		},
	} {
		changed := p
		change(&changed)
		if qualifiedADPDOMHTTPDetail(changed) {
			t.Fatal("unproved source substituted HTTP")
		}
	}
}

func TestRealQualifiedADPDOMHTTPPreservesOriginalParserBrowserAuthorityAndPolicy(t *testing.T) {
	for _, mode := range []string{"success", "header-reserved", "meta-reserved", "unqualified", "invalid-json", "empty-description", "missing-title"} {
		t.Run(mode, func(t *testing.T) {
			ctx := context.Background()
			metadata := `{"scraper_type":"dom","scraper_config":{"render":true,"actions":[{"action":"wait","ms":1}],"steps":[{"tag":"h1","field":"title"},{"tag":"p","attr":"data-field=location","field":"location"},{"tag":"h2","text":"Role","offset":1,"field":"description","html":true}],"defaults":{"employment_type":"FULL_TIME","language":"en"}}}`
			f, authority, claim := independentDetailOwnedFixture(t, metadata, adpDOMFixtureSource, queue.Browser)
			detail, err := authority.ReadWorkdayDetail(ctx, claim)
			if err != nil {
				t.Fatal(err)
			}
			if mode != "unqualified" {
				fp := detail.Profile().EffectiveBoardSHA256
				qualifiedADPDOMHTTPDetails[fp] = adpDOMFixtureScope()
				t.Cleanup(func() { delete(qualifiedADPDOMHTTPDetails, fp) })
			}
			origins, renders := 0, 0
			client := richPipelineHTTP(t, func(w http.ResponseWriter, r *http.Request) {
				origins++
				if r.Method != "GET" || r.URL.Path != "/mascsr/default/careercenter/public/events/staffing/v1/job-requisitions/123_1" || r.URL.Query().Get("cid") != "fixture-client" || r.URL.Query().Get("ccId") != "fixture-center" {
					t.Error("public ADP read changed scope or request")
				}
				if mode == "header-reserved" {
					w.Header().Set("TDM-Reservation", "1")
				}
				w.Header().Set("Content-Type", "application/json")
				if mode == "invalid-json" {
					_, _ = w.Write([]byte("{"))
					return
				}
				description := nativeDOMHTML
				if mode == "missing-title" {
					description = strings.Replace(description, "<h1>Senior Software Engineer</h1>", "", 1)
				}
				if mode == "meta-reserved" {
					description = `<meta name="tdm-reservation" content="1">` + description
				}
				if mode == "empty-description" {
					description = ""
				}
				_ = json.NewEncoder(w).Encode(map[string]any{"requisitionTitle": "API title must not replace the original DOM title", "requisitionDescription": description})
			})
			renderer := heldRenderedDetail(func(ctx context.Context, p queue.WorkdayDetailProfile) (map[string]any, *policy.Reservation, error) {
				renders++
				if mode != "unqualified" {
					return nil, nil, errors.New("qualified route rendered")
				}
				return parseHeldDetail(ctx, p, heldRenderedResult(nativeDOMHTML, p.SourceURL, 200))
			})
			circuits, err := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
			if err != nil {
				t.Fatal(err)
			}
			result, err := RunDetail(ctx, authority, claim, client, richPipelinePreparer(t, f).Processor, circuits, renderer)
			status := "succeeded"
			reserved := strings.HasSuffix(mode, "reserved")
			failed := mode == "invalid-json" || mode == "empty-description" || mode == "missing-title"
			if reserved {
				status = "publisher_reserved"
			}
			if failed {
				status = "failed"
			}
			if err != nil || result == nil || !result.Settled || result.Cycle == nil || result.Cycle.Status != status || mode != "unqualified" && (origins != 1 || renders != 0) || mode == "unqualified" && (origins != 0 || renders != 1) {
				t.Fatal("qualified route or retained renderer failed", err, origins, renders)
			}
			var title string
			var due time.Time
			var sqlReserved bool
			var failures, leases, descriptions int
			if err := f.pg.QueryRow(ctx, `SELECT coalesce(titles[1],''),next_scrape_at,tdm_reserved,scrape_failures,CASE WHEN leased_until IS NULL THEN 0 ELSE 1 END,(SELECT count(*) FROM descriptions WHERE posting_id=p.id) FROM job_posting p WHERE id=$1::uuid`, f.original).Scan(&title, &due, &sqlReserved, &failures, &leases, &descriptions); err != nil {
				t.Fatal(err)
			}
			if sqlReserved != reserved || leases != 0 || failed && (failures != 1 || descriptions != 0) || !failed && failures != 0 || reserved && descriptions != 0 || !failed && !reserved && (title != "Senior Software Engineer" || descriptions != 1) {
				t.Fatal("canonical parser, policy or failure persistence changed")
			}
			if score, err := f.r.ZScore(ctx, "scrapes_browser:workforcenow.adp.com", f.original).Result(); err != nil || score != float64(due.UnixMicro())/1e6 || f.r.ZCard(ctx, "inflight:browser").Val() != 0 || f.r.ZCard(ctx, "scrapes_simple:workforcenow.adp.com").Val() != 0 {
				t.Fatal("original browser queue, deadline or lease changed")
			}
		})
	}
}
