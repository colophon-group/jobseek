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
	for _, provider := range []string{"darwinbox", "bytedance", "brassring", "njoyn"} {
		r.Provider = provider
		if !r.Valid() {
			t.Fatal("fixed provider protocol rejected", provider)
		}
	}
	r.Provider = "arbitrary"
	if r.Valid() {
		t.Fatal("arbitrary provider protocol accepted")
	}
	r.Provider = ""
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

func TestDetailOperationCannotPublishInventoryOrPartialData(t *testing.T) {
	r := Request{Protocol: Protocol, RequestID: strings.Repeat("a", 64), ConfigFingerprint: strings.Repeat("b", 64), BoardURL: "https://example.com/job/1", Metadata: json.RawMessage(`{"fields":{"title":"name"}}`), Operation: "detail", TimeoutMS: 20000}
	if !r.Valid() {
		t.Fatal("detail request rejected")
	}
	r.Provider = "darwinbox"
	if r.Valid() {
		t.Fatal("detail request inherited inventory provider")
	}
	r.Provider = ""
	r.Operation = "unknown"
	if r.Valid() {
		t.Fatal("unknown operation accepted")
	}
	response := Response{Protocol: Protocol, RequestID: r.RequestID, ConfigFingerprint: r.ConfigFingerprint, Outcome: "success", Detail: json.RawMessage(`{"title":"Engineer"}`)}
	if !response.Valid() {
		t.Fatal("complete detail response rejected")
	}
	for _, outcome := range []string{"partial", "failed", "publisher_reserved", "provider_gone", "invalid_config"} {
		response.Outcome = outcome
		if response.Valid() {
			t.Fatal("failure leaked detail", outcome)
		}
	}
	response.Outcome = "success"
	response.Inventory = json.RawMessage(`{}`)
	if response.Valid() {
		t.Fatal("mixed inventory/detail accepted")
	}
}
