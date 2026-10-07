package apireplay

import (
	"encoding/json"
	"fmt"
	"strings"
	"testing"
)

func TestRequestAndResponseBindInventoryToOneCleanedReservation(t *testing.T) {
	r := Request{Protocol: Protocol, RequestID: strings.Repeat("a", 64), ConfigFingerprint: strings.Repeat("b", 64), BoardURL: "https://example.com/careers", Metadata: json.RawMessage(`{"browser":true,"request_headers":{"Authorization":"private-fixture"}}`), TimeoutMS: 15000}
	if !r.Valid() || strings.Contains(fmt.Sprintf("%+v %#v", r, r), "private-fixture") {
		t.Fatal("request bounds/privacy invalid")
	}
	for _, outcome := range []string{"success", "publisher_reserved", "invalid_config", "failed"} {
		response := Response{Protocol: Protocol, RequestID: r.RequestID, ConfigFingerprint: r.ConfigFingerprint, Outcome: outcome}
		if outcome == "success" {
			response.Inventory = json.RawMessage(`{"Jobs":[],"Truncated":false}`)
		}
		if outcome == "publisher_reserved" {
			response.Reservation = &Reservation{URL: "https://example.com/api", Source: "meta"}
		}
		if !response.Valid() {
			t.Fatal("valid terminal outcome rejected", outcome)
		}
		if outcome != "success" {
			response.Inventory = json.RawMessage(`{"Jobs":[{}]}`)
			if response.Valid() {
				t.Fatal("failure published partial inventory")
			}
		}
	}
	r.TimeoutMS = MaxDurationMS + 1
	if r.Valid() {
		t.Fatal("unbounded conversation")
	}
	var decoded Request
	if Decode([]byte(`{"protocol":"x","unknown":1}`), RequestLimit, &decoded) == nil {
		t.Fatal("unknown request accepted")
	}
}
