package executor

import (
	"bytes"
	"context"
	"errors"
	"testing"

	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/lightpandaadapter"
	lp "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/lightpandaclient"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
	"google.golang.org/protobuf/proto"
)

type largeDocumentTestRunner struct{ body []byte }

func (r largeDocumentTestRunner) Run(_ context.Context, bound lightpandaadapter.BoundInput) lightpandaadapter.RunnerOutcome {
	status := uint32(200)
	return lightpandaadapter.NewRunnerSuccess(bound, &lightpandaadapter.RawSuccess{FinalURL: bound.Input().Plan.TargetUrl, Status: &status, HTML: r.body, ResourcePolicy: &runtimev1.ResourcePolicySignals{}})
}

func TestLargeDocumentConsumerKeepsLegacyBoundsIntegrityAndPublisherPolicy(t *testing.T) {
	const source = "https://example.test/jobs"
	for _, mode := range []string{"complete", "corrupt", "incomplete", "oversized-manifest", "metadata-reservation", "outside-original-metadata-window"} {
		t.Run(mode, func(t *testing.T) {
			body := []byte("<html><body>")
			if mode == "metadata-reservation" {
				body = append(body, []byte(`<meta name="tdm-reservation" content="1">`)...)
			}
			body = append(body, bytes.Repeat([]byte("x"), 2_800_000)...)
			if mode == "outside-original-metadata-window" {
				body = append(body, []byte(`<meta name="tdm-reservation" content="1">`)...)
			}
			body = append(body, []byte("</body></html>")...)
			input, err := lp.NavigationInput(lp.Navigation{URL: source, RoutingRevision: "large-document", OriginRequestID: "large-document", Wait: "load", TimeoutMS: 1000})
			if err != nil {
				t.Fatal(err)
			}
			adapter, err := lightpandaadapter.NewLargeDocumentSessionRenderOnly(largeDocumentTestRunner{body}, nil, nil)
			if err != nil {
				t.Fatal(err)
			}
			result := adapter.Execute(context.Background(), input)
			if result.GetSuccess() == nil {
				t.Fatal("large document fixture rejected")
			}
			payload, err := proto.MarshalOptions{Deterministic: true}.Marshal(result)
			if err != nil {
				t.Fatal(err)
			}
			if _, err := DecodeResult(payload); !errors.Is(err, ErrProtocol) {
				t.Fatal("large document escaped legacy result limit")
			}
			result, err = DecodeDocumentResult(payload)
			if err != nil {
				t.Fatal("explicit document decode rejected", err)
			}
			if _, err := RenderedHTML(result, source); !errors.Is(err, ErrRenderedResult) {
				t.Fatal("large document escaped legacy HTML limit")
			}
			manifest := result.GetSuccess().Html
			switch mode {
			case "corrupt":
				manifest.Chunks[0].GetInlineBody()[0] ^= 1
			case "incomplete":
				manifest.Complete = false
			case "oversized-manifest":
				manifest.TotalSizeBytes = 4_000_001
			}
			got, err := RenderedDocumentHTML(result, source)
			if mode == "complete" || mode == "outside-original-metadata-window" {
				if err != nil || !bytes.Equal([]byte(got), body) {
					t.Fatal("complete large document lost", err)
				}
			} else if mode == "metadata-reservation" {
				var reservation *policy.Reservation
				if !errors.As(err, &reservation) || got != "" {
					t.Fatal("publisher reservation ignored")
				}
			} else if !errors.Is(err, ErrRenderedResult) || got != "" {
				t.Fatal("invalid manifest published partial content")
			}
		})
	}
}
