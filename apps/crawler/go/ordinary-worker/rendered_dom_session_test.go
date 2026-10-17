package worker

import (
	"context"
	"errors"
	"testing"

	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	executor "github.com/colophon-group/jobseek/apps/crawler/go/lightpanda-b0-executor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	"google.golang.org/protobuf/proto"
)

func heldDOMPageBytes(t *testing.T, endpoint, body string, status int, header string) []byte {
	t.Helper()
	v := heldRenderedResult("", endpoint, uint32(status))
	v.GetSuccess().Captures = []*runtimev1.CapturedValue{{CaptureId: "dom-page", Body: heldRenderedResult(body, endpoint, uint32(status)).GetSuccess().Html}}
	if header != "" {
		v.GetSuccess().ResourcePolicy.TdmReservationHeader = &header
	}
	b, e := (proto.MarshalOptions{Deterministic: true}).Marshal(v)
	if e != nil {
		t.Fatal(e)
	}
	return b
}

func TestHeldDOMPageKeepsTypedSourcePolicyChallengeAndTerminalStatus(t *testing.T) {
	p := queue.GreenhouseMonitorProfile{Endpoint: "https://example.com/careers"}
	// Use the same pure listing parser as the compiled profile.
	config := map[string]string{"crawler_type": "dom", "monitor_needs_browser": "1", "board_url": p.Endpoint, "metadata": `{"render":true,"pagination":{"browser":true,"param_name":"page"},"url_filter":"/jobs/"}`}
	listing, _, e := queue.RenderedDOMMonitorOptions(config)
	if e != nil {
		t.Fatal(e)
	}
	for _, mode := range []string{"jobs", "empty200", "404", "400", "503", "challenge403", "header-policy", "meta-policy", "foreign-redirect", "corrupt-manifest", "wrong-capture"} {
		t.Run(mode, func(t *testing.T) {
			body, status, header, endpoint := `<a href="/jobs/tail">Tail</a>`, 200, "", p.Endpoint
			switch mode {
			case "empty200":
				body = ""
			case "404":
				status = 404
			case "400":
				status = 400
			case "503":
				status = 503
			case "challenge403":
				status = 403
				body = `<title>Just a moment...</title><div id="cf-chl-widget">Checking your browser</div>`
			case "header-policy":
				header = "1"
			case "meta-policy":
				body = `<meta name="tdm-reservation" content="1">`
			case "foreign-redirect":
				endpoint = "https://foreign.example/careers"
			}
			payload := heldDOMPageBytes(t, endpoint, body, status, header)
			if mode == "corrupt-manifest" || mode == "wrong-capture" {
				var v runtimev1.BrowserResult
				if proto.Unmarshal(payload, &v) != nil {
					t.Fatal("fixture")
				}
				if mode == "wrong-capture" {
					v.GetSuccess().Captures[0].CaptureId = "feed"
				} else {
					v.GetSuccess().Captures[0].Body.Complete = false
				}
				payload, _ = (proto.MarshalOptions{Deterministic: true}).Marshal(&v)
			}
			found, e := parseHeldDOMPage(context.Background(), p, listing, payload, true)
			switch mode {
			case "jobs":
				if e != nil || len(found.Jobs) != 1 {
					t.Fatal("tail not parsed", e)
				}
			case "404", "400":
				if e != nil || len(found.Jobs) != 0 {
					t.Fatal("terminal status changed", e)
				}
			case "header-policy", "meta-policy":
				if found.Response == nil || !found.Response.reserved {
					t.Fatal("publisher policy lost", e)
				}
			case "challenge403":
				if !errors.Is(e, executor.ErrBotChallenge) {
					t.Fatal("challenge became terminal absence", e)
				}
			default:
				if e == nil {
					t.Fatal("invalid page accepted")
				}
			}
		})
	}
}
