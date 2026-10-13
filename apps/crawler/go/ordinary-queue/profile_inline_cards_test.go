package queue

import (
	"encoding/json"
	"os"
	"testing"
)

func TestMediaMarktInlineCardsBindExactOriginalConfiguration(t *testing.T) {
	b, e := os.ReadFile("testdata/inline_mediamarkt_metadata.json")
	if e != nil {
		t.Fatal(e)
	}
	c := map[string]string{"crawler_type": "inline", "monitor_needs_browser": "1", "board_url": inlineCardBoardURL, "metadata": string(b)}
	cards, e := InlineCardOptions(c)
	if e != nil || cards.ContentIndex != 1 {
		t.Fatal("original card config refused", e)
	}
	o, _, e := RenderedInlineMonitorOptions(c)
	if e != nil || o.Item["tag"] != "jobseek-inline-detail" {
		t.Fatal("parser clone lost expanded boundary", e)
	}
	var md map[string]any
	json.Unmarshal(b, &md)
	md["detail_identity_attribute"] = "name"
	b, _ = json.Marshal(md)
	c["metadata"] = string(b)
	if _, e := InlineCardOptions(c); e == nil {
		t.Fatal("changed card controls gained source authority")
	}
	if _, _, e := RenderedInlineMonitorOptions(c); e == nil {
		t.Fatal("changed original metadata gained rendered authority")
	}
}
