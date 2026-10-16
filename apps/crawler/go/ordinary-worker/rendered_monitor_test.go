package worker

import (
	"context"
	"fmt"
	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	"net/http"
	"reflect"
	"strings"
	"testing"
)

type heldMonitor func(context.Context, queue.GreenhouseMonitorProfile, map[string]string) (RichDiscovery, error)

func (f heldMonitor) FetchMonitor(c context.Context, p queue.GreenhouseMonitorProfile, m map[string]string, _ *http.Client) (RichDiscovery, error) {
	return f(c, p, m)
}

func TestRenderedListingBrowserURLIdentity(t *testing.T) {
	html := `<base href="../jobs/"><base href="https://ignored.test/"><a href="工程師?q=a b#details">One</a><a href="/jobs/first" href="/jobs/second">First attr</a><a href="https://EXAMPLE.com:443/jobs/42">Default port</a><a href="\jobs\backslash">Slash</a><a href="mailto:jobs@example.com">Mail</a><a href="">Empty</a>`
	got, e := renderedListingURLs(html, "https://example.com/listing/index", "")
	want := []string{"https://example.com/jobs/%E5%B7%A5%E7%A8%8B%E5%B8%AB?q=a%20b#details", "https://example.com/jobs/first", "https://example.com/jobs/42", "https://example.com/jobs/backslash", "https://example.com/jobs/"}
	if e != nil || !reflect.DeepEqual(got, want) {
		t.Fatal(got, e)
	}
}

func TestRenderedListingGroupedPDFSelectorsKeepBrowserOrder(t *testing.T) {
	selector := `.title-pdf-home-section a[href*='.pdf'], section.jobfold a[href*='.pdf']`
	source := `<base href="/documents/"><section class="jobfold title-pdf-home-section"><a href="first.pdf">First</a></section><div class="title-pdf-home-section"><a href="second.pdf">Second</a></div><section class="jobfold"><a href="third.pdf">Third</a></section><a href="outside.pdf">Outside</a>`
	got, err := renderedListingURLs(source, "https://example.com/careers", selector)
	want := []string{"https://example.com/documents/first.pdf", "https://example.com/documents/second.pdf", "https://example.com/documents/third.pdf"}
	if err != nil || !reflect.DeepEqual(got, want) {
		t.Fatal("browser selector group order or overlap changed", got, err)
	}
	for _, invalid := range []string{selector + ",", "a, section"} {
		if _, err := renderedListingURLs(source, "https://example.com/careers", invalid); err == nil {
			t.Fatal("invalid or non-link selector group accepted")
		}
	}
}

func TestRenderedListingExcludesTemplateContentsAndForeignBase(t *testing.T) {
	// HTML template contents are a DocumentFragment, outside the document
	// tree: https://html.spec.whatwg.org/multipage/scripting.html#the-template-element
	source := `<template><base href="https://inert.test/"><a href="/jobs/inert">Inert</a><template><a href="/jobs/nested">Nested</a></template></template><svg><base href="https://foreign.test/"></base></svg><base href="../jobs/"><a href="active">Active</a>`
	got, err := renderedListingURLs(source, "https://example.com/listing/index", "")
	if err != nil || !reflect.DeepEqual(got, []string{"https://example.com/jobs/active"}) {
		t.Fatal(got, err)
	}
	got, err = renderedListingURLs(source, "https://example.com/listing/index", "template a")
	if err != nil || len(got) != 0 {
		t.Fatal("template fragment participated in document selection", got, err)
	}
}

func TestRealRenderedMonitorInventoryPolicyFailureAndSettlement(t *testing.T) {
	for _, mode := range []string{"success", "actions", "browser-details", "503", "empty", "gone404", "gone410", "header", "meta", "challenge", "malformed", "cancelled", "proxy-success", "proxy-malformed", "proxy-header", "proxy-503", "proxy-gone404", "proxy-cancelled"} {
		t.Run(mode, func(t *testing.T) {
			useProxy := strings.HasPrefix(mode, "proxy-")
			outcomeMode := strings.TrimPrefix(mode, "proxy-")
			metadata := `{"render":true,"url_filter":{"include":"/jobs/","exclude":"intern"},"scraper_type":"json-ld"}`
			if outcomeMode == "actions" {
				metadata = strings.Replace(metadata, `{"render":true,`, `{"render":true,"actions":[{"action":"wait","ms":0},{"action":"evaluate","script":"() => window.ready = true","required":true}],`, 1)
			}
			detailWorker := queue.Simple
			if outcomeMode == "browser-details" {
				detailWorker = queue.Browser
				metadata = `{"render":true,"url_filter":{"include":"/jobs/","exclude":"intern"},"scraper_type":"json-ld","scraper_config":{"render":true}}`
			}
			if useProxy {
				metadata = strings.Replace(metadata, `"render":true`, `"render":true,"proxy":true,"transport_attempts":3`, 1)
			}
			f := privateRichPipelineFixture(t, "dom", metadata, queue.Browser, detailWorker)
			ctx := context.Background()
			f.pg.Exec(ctx, "UPDATE job_posting SET missing_count=3 WHERE id=$1::uuid", f.original)
			if !f.a.RequiresRenderedDetails() {
				t.Fatal("browser monitor hidden from startup")
			}
			if claim, e := f.a.Claim(ctx, queue.Simple); e != nil || claim != nil {
				t.Fatal("simple claimed browser", e)
			}
			claim, e := f.a.Claim(ctx, queue.Browser)
			if e != nil || claim == nil {
				t.Fatal("browser not claimed", e)
			}
			circuits, e := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
			if e != nil {
				t.Fatal(e)
			}
			calls := 0
			renderer := heldMonitor(func(ctx context.Context, p queue.GreenhouseMonitorProfile, c map[string]string) (RichDiscovery, error) {
				calls++
				if p.BrowserProxy != useProxy {
					t.Fatal("monitor lost compiled proxy policy")
				}
				status := uint32(200)
				html := `<base href="/jobs/"><a href="工程師">Engineer</a><a href="intern">Intern</a><a href="/careers#jobs">Self</a>`
				switch outcomeMode {
				case "503":
					status = 503
				case "empty":
					html = "<p>No jobs</p>"
				case "gone404":
					status = 404
				case "gone410":
					status = 410
				case "meta":
					html = `<meta name="tdm-reservation" content="1"><meta name="tdm-policy" content="policy">`
				case "challenge":
					html = `<title>Just a moment...</title><div id="cf-chl-widget">Checking your browser</div>`
				}
				value := heldRenderedResult(html, "https://example.com/careers", status)
				if outcomeMode == "header" {
					v := "1"
					value.GetSuccess().ResourcePolicy = &runtimev1.ResourcePolicySignals{TdmReservationHeader: &v}
					status = 404
					value.GetSuccess().Status = &status
				}
				if outcomeMode == "malformed" {
					value.GetSuccess().Html.Complete = false
				}
				if outcomeMode == "cancelled" {
					return RichDiscovery{}, context.Canceled
				}
				return parseHeldRenderedMonitor(ctx, p, c, value)
			})
			client := richPipelineHTTP(t, func(http.ResponseWriter, *http.Request) { t.Error("browser issued direct HTTP") })
			client.proxyRequired = useProxy
			if required, err := runtimeClaimUsesProxy(ctx, f.a, claim); err != nil || required != useProxy || f.a.RequiresProxyHTTP() != useProxy {
				t.Fatal("canonical monitor selected wrong proxy transport", err)
			}
			r, e := RunGreenhouseClaim(ctx, f.a, claim, client, &pipelinePreparer{}, circuits, renderer)
			if e != nil || !r.Settled || calls != 1 {
				t.Fatal(r, e)
			}
			var active, reserved bool
			var missing, count, failures int
			f.pg.QueryRow(ctx, "SELECT is_active,missing_count FROM job_posting WHERE id=$1::uuid", f.original).Scan(&active, &missing)
			f.pg.QueryRow(ctx, "SELECT tdm_reserved,consecutive_failures,(SELECT count(*) FROM job_posting WHERE board_id=$1::uuid) FROM job_board WHERE id=$1::uuid", f.board).Scan(&reserved, &failures, &count)
			if outcomeMode == "success" || outcomeMode == "actions" || outcomeMode == "browser-details" {
				if r.Batches.Inserted != 1 || active || missing != 4 || count != 2 || failures != 0 {
					t.Fatal("inventory lost", r)
				}
				var source string
				f.pg.QueryRow(ctx, "SELECT source_url FROM job_posting WHERE board_id=$1::uuid AND id<>$2::uuid", f.board, f.original).Scan(&source)
				if !strings.Contains(source, "%E5%B7%A5") {
					t.Fatal("browser identity lost", source)
				}
				if f.r.ZCard(ctx, "ft_scrapes_"+string(detailWorker)+":example.com").Val() != 1 {
					t.Fatal("urgent detail routed to wrong namespace")
				}
			} else if outcomeMode == "empty" {
				if count != 1 || r.Cycle.Status != "succeeded" {
					t.Fatal(r)
				}
			} else {
				if !active || missing != 3 || count != 1 {
					t.Fatal("failed inventory changed canonical postings", mode)
				}
				if outcomeMode == "meta" || outcomeMode == "header" {
					if !reserved || failures != 0 || r.Cycle.Status != "publisher_reserved" {
						t.Fatal(r)
					}
				} else if strings.HasPrefix(outcomeMode, "gone") {
					if r.Cycle.Status != "gone_pending" {
						t.Fatal(r)
					}
				} else if failures != 1 {
					t.Fatal("failed fetch not recorded", fmt.Sprint(r))
				}
			}
			if outcomeMode == "503" && f.r.Exists(ctx, "host_fail:example.com").Val() != 1 {
				t.Fatal("origin503 not attributed")
			}
			if (outcomeMode == "malformed" || outcomeMode == "cancelled" || outcomeMode == "challenge") && f.r.Exists(ctx, "host_fail:example.com").Val() != 0 {
				t.Fatal("renderer/parser failure blamed origin")
			}
			if f.r.ZCard(ctx, "inflight:browser").Val() != 0 || f.r.ZCard(ctx, "monitors_simple:dom").Val() != 0 {
				t.Fatal("wrong namespace settlement")
			}
		})
	}
}
