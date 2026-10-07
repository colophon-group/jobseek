package worker

import (
	"context"
	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	"testing"
)

func TestRenderedFeedRejectsIncompleteOrCorruptManifests(t *testing.T) {
	for _, mode := range []string{"html", "body", "hash", "sequence", "size", "capture"} {
		t.Run(mode, func(t *testing.T) {
			endpoint := "https://example.com/feed"
			v := heldRenderedResult("", endpoint, 200)
			v.GetSuccess().Captures = []*runtimev1.CapturedValue{{CaptureId: "feed", Body: heldRenderedResult(`<rss><channel/></rss>`, endpoint, 200).GetSuccess().Html}}
			body := v.GetSuccess().Captures[0].Body
			switch mode {
			case "html":
				v.GetSuccess().Html.Complete = false
			case "body":
				body.Complete = false
			case "hash":
				body.TotalSha256 = "invalid"
			case "sequence":
				body.Chunks[0].Sequence = 1
			case "size":
				body.Chunks[0].SizeBytes++
			case "capture":
				v.GetSuccess().Captures[0].CaptureId = "foreign"
			}
			if _, e := parseRenderedRSSPage(context.Background(), endpoint, v, "generic", false); e == nil {
				t.Fatal("invalid raw feed accepted")
			}
		})
	}
}
