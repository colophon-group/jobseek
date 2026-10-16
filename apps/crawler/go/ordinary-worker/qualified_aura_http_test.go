package worker

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"strings"
	"testing"
	"time"

	executor "github.com/colophon-group/jobseek/apps/crawler/go/lightpanda-b0-executor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
)

func TestQualifiedAuraHTTPBindsFrozenConfigOriginAndJobQuery(t *testing.T) {
	if len(qualifiedAuraHTTPDetails) != 1 {
		t.Fatal("one qualified full configuration required")
	}
	for fingerprint, route := range qualifiedAuraHTTPDetails {
		original := queue.WorkdayDetailProfile{Profile: "dom.rendered-detail/v1", EffectiveBoardSHA256: fingerprint, SourceURL: "https://" + route.host + route.path + "?jobReq=example"}
		if !qualifiedAuraHTTPDetail(original) {
			t.Fatal("qualified source refused")
		}
		for _, change := range []func(*queue.WorkdayDetailProfile){
			func(p *queue.WorkdayDetailProfile) { p.EffectiveBoardSHA256 = strings.Repeat("0", 64) },
			func(p *queue.WorkdayDetailProfile) { p.Profile = "dom.direct-detail/v1" },
			func(p *queue.WorkdayDetailProfile) { p.SkipSSL = true },
			func(p *queue.WorkdayDetailProfile) {
				p.SourceURL = "http://" + route.host + route.path + "?jobReq=example"
			},
			func(p *queue.WorkdayDetailProfile) {
				p.SourceURL = "https://foreign.example" + route.path + "?jobReq=example"
			},
			func(p *queue.WorkdayDetailProfile) {
				p.SourceURL = "https://" + route.host + ".foreign.example" + route.path + "?jobReq=example"
			},
			func(p *queue.WorkdayDetailProfile) {
				p.SourceURL = "https://user:secret@" + route.host + route.path + "?jobReq=example"
			},
			func(p *queue.WorkdayDetailProfile) {
				p.SourceURL = "https://" + route.host + ":444" + route.path + "?jobReq=example"
			},
			func(p *queue.WorkdayDetailProfile) { p.SourceURL += "#fragment" },
			func(p *queue.WorkdayDetailProfile) { p.SourceURL += "&jobReq=second" },
			func(p *queue.WorkdayDetailProfile) { p.SourceURL = "https://" + route.host + route.path + "?jobReq=" },
			func(p *queue.WorkdayDetailProfile) {
				p.SourceURL = "https://" + route.host + route.path + "?jobReq=%00"
			},
			func(p *queue.WorkdayDetailProfile) {
				p.SourceURL = "https://" + route.host + route.path + "?jobReq=%0A"
			},
			func(p *queue.WorkdayDetailProfile) {
				p.SourceURL = "https://" + route.host + route.path + "?jobReq=" + strings.Repeat("a", 1025)
			},
			func(p *queue.WorkdayDetailProfile) { p.SourceURL = "https://" + route.host + "/s/other?jobReq=example" },
		} {
			changed := original
			change(&changed)
			if qualifiedAuraHTTPDetail(changed) {
				t.Fatal("unqualified source or configuration selected public reader")
			}
		}
	}
}

func auraHTTPFixture(t *testing.T, mode string) (*VerifiedDirectHTTP, *int) {
	t.Helper()
	calls := new(int)
	return richPipelineHTTP(t, func(w http.ResponseWriter, r *http.Request) {
		*calls++
		if r.Header.Get("Authorization") != "" || r.Header.Get("Cookie") != "" {
			t.Error("anonymous reader sent credentials")
		}
		switch r.URL.Path {
		case "/s/details":
			if r.Method != "GET" || r.URL.Query().Get("jobReq") != "example" {
				t.Error("canonical source request changed")
			}
			if mode == "root-404" {
				w.WriteHeader(404)
				return
			}
			if mode == "root-reserved" || mode == "header-overridden" {
				w.Header().Set("TDM-Reservation", "1")
			}
			if mode == "oversized" {
				_, _ = w.Write([]byte(strings.Repeat("x", 4_000_001)))
				return
			}
			if mode == "invalid-utf8" {
				_, _ = w.Write([]byte{0xff})
				return
			}
			app := "siteforce:communityApp"
			if mode == "wrong-context" {
				app = "foreign:application"
			}
			config := map[string]any{"context": map[string]any{"mode": "PROD", "app": app, "fwuid": "fresh-framework", "contextPath": "/s/sfsites", "loaded": map[string]string{"APPLICATION@markup://siteforce:communityApp": "fresh-app"}, "uad": true, "scriptNonce": "must-not-be-forwarded"}, "auraCmpDefBaseURI": "/auraCmpDef?_au=fresh-app&aura.mode=PROD"}
			body, _ := json.Marshal(config)
			if mode == "root-meta-reserved" {
				_, _ = w.Write([]byte(`<meta name="tdm-reservation" content="1">`))
			}
			if mode == "header-overridden" {
				_, _ = w.Write([]byte(`<meta name="tdm-reservation" content="0">`))
			}
			_, _ = w.Write(append([]byte("<script>var auraConfig = "), append(body, []byte(";</script>")...)...))
		case "/s/sfsites/auraCmpDef":
			if r.Method != "GET" || r.URL.Query().Get("_def") != "markup://c:jobDetail" || r.URL.Query().Get("_au") != "fresh-app" {
				t.Error("component request escaped its fixed read scope")
			}
			if mode == "redirect-reserved" {
				w.Header().Set("TDM-Reservation", "\t1\t")
				w.Header().Set("Location", r.URL.String())
				w.WriteHeader(302)
				return
			}
			if mode == "foreign-redirect" {
				w.Header().Set("Location", "https://foreign.example/s/sfsites/auraCmpDef")
				w.WriteHeader(302)
				return
			}
			if mode == "redirect-loop" {
				w.Header().Set("Location", r.URL.String())
				w.WriteHeader(302)
				return
			}
			_, _ = w.Write([]byte(`$A.getContext().mergeGVPs([{"type":"$Label","values":{"c":{"DiscloseCompDetailsList":"New York,Poland"}}}]);`))
		case "/s/sfsites/aura":
			if r.Method != "POST" || r.Header.Get("Origin") != "https://example.com" || r.Header.Get("Referer") != "https://example.com/s/details?jobReq=example" {
				t.Error("read RPC origin or method changed")
			}
			if err := r.ParseForm(); err != nil {
				t.Error("invalid read form")
			}
			if r.PostForm.Get("aura.token") != "null" || r.PostForm.Get("aura.pageURI") != "/s/details?jobReq=example" {
				t.Error("anonymous request token/page changed")
			}
			var context map[string]any
			if json.Unmarshal([]byte(r.PostForm.Get("aura.context")), &context) != nil || len(context) != 7 || context["fwuid"] != "fresh-framework" || context["scriptNonce"] != nil {
				t.Error("fresh anonymous context was not narrowed")
			}
			var message struct {
				Actions []struct {
					ID, Descriptor, CallingDescriptor string
					Params                            struct {
						Namespace, Classname, Method string
						Params                       map[string]string
					}
				}
			}
			if json.Unmarshal([]byte(r.PostForm.Get("message")), &message) != nil || len(message.Actions) != 1 {
				t.Error("read action shape changed")
				w.WriteHeader(400)
				return
			}
			action := message.Actions[0]
			if action.Descriptor != "aura://ApexActionController/ACTION$execute" || action.CallingDescriptor != "UNKNOWN" || action.Params.Classname != "rolePage" || action.Params.Namespace != "" || len(action.Params.Params) != 1 {
				t.Error("RPC escaped fixed public reader")
			}
			if mode == "rpc-redirect" {
				w.Header().Set("Location", "/s/other")
				w.WriteHeader(302)
				return
			}
			if mode == "rpc-reserved" {
				w.Header().Set("TDM-Reservation", "1")
			}
			var value any
			switch action.Params.Method {
			case "getJobByJobReqTitleURL":
				if action.Params.Params["req"] != "example" {
					t.Error("job source changed")
				}
				value = map[string]any{"Id": "a01000000000001AAA", "Is_Campus_Campaign_Event__c": false, "Currency__c": "USD", "Base_Compensation_Low__c": 100000, "Base_Compensation_High__c": 120000, "Frequency__c": "Annually"}
			case "getLocation":
				if action.Params.Params["req"] != "a01000000000001AAA" {
					t.Error("resolved job binding changed")
				}
				value = []map[string]string{{"Name": "New York"}}
			case "getDescription":
				description := "<p>Build reliable public services.</p>"
				if mode == "description-reserved" {
					description = `<meta name="tdm-reservation" content="1">` + description
				}
				value = map[string]string{"Description__c": description}
			default:
				t.Error("unobserved RPC invoked")
				w.WriteHeader(400)
				return
			}
			id, state := action.ID, "SUCCESS"
			if mode == "wrong-rpc-id" {
				id = "foreign"
			}
			if mode == "rpc-error" {
				state = "ERROR"
			}
			_, _ = w.Write([]byte("while(1);"))
			_ = json.NewEncoder(w).Encode(map[string]any{"actions": []any{map[string]any{"id": id, "state": state, "returnValue": map[string]any{"returnValue": value}}}})
		default:
			t.Error("unexpected origin path")
			w.WriteHeader(404)
		}
	}), calls
}

func TestAuraHTTPTransportPreservesPublicScopePolicyBoundsAndCorrelation(t *testing.T) {
	for _, test := range []struct {
		mode       string
		calls      int
		reserved   bool
		success    bool
		navigation bool
	}{
		{"success", 5, false, true, false},
		{"header-overridden", 5, false, true, false},
		{"root-reserved", 1, true, false, false},
		{"root-meta-reserved", 1, true, false, false},
		{"redirect-reserved", 2, true, false, false},
		{"rpc-reserved", 3, true, false, false},
		{"description-reserved", 5, true, false, false},
		{"foreign-redirect", 2, false, false, false},
		{"redirect-loop", 5, false, false, false},
		{"rpc-redirect", 3, false, false, false},
		{"wrong-rpc-id", 3, false, false, false},
		{"rpc-error", 3, false, false, false},
		{"wrong-context", 1, false, false, false},
		{"oversized", 1, false, false, false},
		{"invalid-utf8", 1, false, false, false},
		{"root-404", 1, false, false, true},
	} {
		t.Run(test.mode, func(t *testing.T) {
			client, calls := auraHTTPFixture(t, test.mode)
			document, reservation, err := fetchAuraDocument(context.Background(), client, "https://example.com/s/details?jobReq=example")
			if *calls != test.calls || (reservation != nil) != test.reserved || (err == nil) != test.success {
				t.Fatal("transport/policy outcome changed", *calls, err)
			}
			if test.success && (!strings.Contains(document, "Build reliable public services.") || !strings.Contains(document, "USD 100000 and USD 120000 annually") || !strings.Contains(document, "employee benefits")) {
				t.Fatal("successful API omitted description or compensation")
			}
			var navigation *executor.NavigationHTTPError
			if errors.As(err, &navigation) != test.navigation {
				t.Fatal("canonical source status semantics changed")
			}
		})
	}
}

func TestAuraHTTPHonorsCancellationBeforeOrigin(t *testing.T) {
	client, calls := auraHTTPFixture(t, "success")
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	_, _, err := fetchAuraDocument(ctx, client, "https://example.com/s/details?jobReq=example")
	if !errors.Is(err, context.Canceled) || *calls != 0 {
		t.Fatal("cancelled task made an origin request")
	}
}

func TestRealQualifiedAuraHTTPPreservesBrowserAuthorityPolicyAndSettlement(t *testing.T) {
	for _, test := range []struct {
		mode     string
		requests int
		status   string
	}{
		{"success", 5, "succeeded"},
		{"root-reserved", 1, "publisher_reserved"},
		{"redirect-reserved", 2, "publisher_reserved"},
		{"rpc-reserved", 3, "publisher_reserved"},
		{"description-reserved", 5, "publisher_reserved"},
		{"wrong-rpc-id", 3, "failed"},
		{"canonical-reserved", 0, "publisher_reserved"},
		{"unqualified", 0, "succeeded"},
	} {
		t.Run(test.mode, func(t *testing.T) {
			ctx := context.Background()
			metadata := `{"scraper_type":"dom","scraper_config":{"enrich":["description"],"render":true,"wait":"load","timeout":60000,"steps":[{"tag":"h2"},{"tag":"p","html":true,"stop":"Apply","field":"description","offset":2}]}}`
			f, authority, claim := independentDetailOwnedFixture(t, metadata, "https://example.com/s/details?jobReq=example", queue.Browser)
			detail, err := authority.ReadWorkdayDetail(ctx, claim)
			if err != nil {
				t.Fatal(err)
			}
			if test.mode != "unqualified" {
				fingerprint := detail.Profile().EffectiveBoardSHA256
				qualifiedAuraHTTPDetails[fingerprint] = auraHTTPDetailRoute{host: "example.com", path: "/s/details"}
				t.Cleanup(func() { delete(qualifiedAuraHTTPDetails, fingerprint) })
			}
			if test.mode == "canonical-reserved" {
				if _, err := f.pg.Exec(ctx, "UPDATE job_posting SET tdm_reserved=true WHERE id=$1::uuid", f.original); err != nil {
					t.Fatal(err)
				}
			}
			client, calls := auraHTTPFixture(t, test.mode)
			renders := 0
			renderer := heldRenderedDetail(func(context.Context, queue.WorkdayDetailProfile) (map[string]any, *policy.Reservation, error) {
				renders++
				if test.mode != "unqualified" {
					return nil, nil, errors.New("qualified HTTP source unexpectedly rendered")
				}
				return map[string]any{"description": "Retained rendered original."}, nil, nil
			})
			circuits, err := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
			if err != nil {
				t.Fatal(err)
			}
			result, err := RunDetail(ctx, authority, claim, client, richPipelinePreparer(t, f).Processor, circuits, renderer)
			if err != nil || result == nil || !result.Settled || result.Cycle == nil || result.Cycle.Status != test.status || *calls != test.requests || test.mode == "unqualified" && renders != 1 || test.mode != "unqualified" && renders != 0 {
				t.Fatal("API substitution changed authority, request budget or settlement", err, *calls, renders)
			}
			var source string
			var due time.Time
			var lease *time.Time
			var reserved, active bool
			var failures, descriptions int
			if err := f.pg.QueryRow(ctx, `SELECT source_url,next_scrape_at,leased_until,tdm_reserved,is_active,scrape_failures,(SELECT count(*) FROM descriptions WHERE posting_id=p.id) FROM job_posting p WHERE id=$1::uuid`, f.original).Scan(&source, &due, &lease, &reserved, &active, &failures, &descriptions); err != nil {
				t.Fatal(err)
			}
			if source != "https://example.com/s/details?jobReq=example" || lease != nil || !active || reserved != (test.status == "publisher_reserved") || test.status == "failed" && failures != 1 || test.status != "failed" && failures != 0 || test.status == "succeeded" && descriptions != 1 || test.status != "succeeded" && descriptions != 0 {
				t.Fatal("canonical source, rights, failure or lease effects changed")
			}
			if test.mode == "success" {
				var stored string
				if err := f.pg.QueryRow(ctx, "SELECT html FROM descriptions WHERE posting_id=$1::uuid", f.original).Scan(&stored); err != nil || !strings.Contains(stored, "Build reliable public services.") || !strings.Contains(stored, "USD 100000 and USD 120000 annually") || !strings.Contains(stored, "employee benefits") {
					t.Fatal("canonical description lost API content or compensation")
				}
			}
			if score, err := f.r.ZScore(ctx, "scrapes_browser:example.com", f.original).Result(); err != nil || score != float64(due.UnixMicro())/1e6 || f.r.ZCard(ctx, "inflight:browser").Val() != 0 || f.r.ZCard(ctx, "scrapes_simple:example.com").Val() != 0 {
				t.Fatal("HTTP substitution changed the original browser lane, deadline or lease")
			}
		})
	}
}
