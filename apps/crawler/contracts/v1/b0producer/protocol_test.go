package b0producer

import (
	"encoding/json"
	"errors"
	"os"
	"strings"
	"testing"

	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/framing"
)

func TestFrozenActualPythonProducerProtocol(t *testing.T) {
	body, err := os.ReadFile("testdata/python_protocol.json")
	if err != nil {
		t.Fatal(err)
	}
	var corpus struct {
		Responses []struct {
			Name, Body, Outcome, Reason string
			Accepted, Capacity          bool
		}
		Requests []struct {
			Body    string
			Request Request
		}
	}
	if json.Unmarshal(body, &corpus) != nil || len(corpus.Responses) < 40 || len(corpus.Requests) != 5 {
		t.Fatal("actual Python protocol corpus unavailable")
	}
	for _, test := range corpus.Responses {
		t.Run(test.Name, func(t *testing.T) {
			got, err := DecodeResponse([]byte(test.Body))
			if test.Accepted {
				if err != nil || got.Outcome != test.Outcome {
					t.Fatal("native decision differs from actual Python", err)
				}
				return
			}
			if err == nil || strings.Contains(err.Error(), "secret") {
				t.Fatal("invalid producer decision accepted/exposed")
			}
			var capacity *CapacityError
			if errors.As(err, &capacity) != test.Capacity || test.Capacity && capacity.Reason != test.Reason {
				t.Fatal("actual capacity decision lost")
			}
		})
	}
	for _, test := range corpus.Requests {
		got, err := EncodeRequest(test.Request)
		if err != nil || string(got) != test.Body {
			t.Fatal("native request differs from actual Python", err)
		}
	}
}

func TestNativeRequestCapIncludesActualVarintPrefix(t *testing.T) {
	r := taskRequest(Task{Domain: "jobs.example.test", PostingID: "task", Config: map[string]string{"value": ""}}, "prepare", "", true)
	base, err := EncodeRequest(r)
	if err != nil {
		t.Fatal(err)
	}
	r.Config["value"] = strings.Repeat("x", int(FrameLimit)-3-len(base))
	body, err := EncodeRequest(r)
	if err != nil || uint64(len(body))+framing.UvarintSize(uint64(len(body))) != FrameLimit {
		t.Fatal("exact prefix-inclusive boundary rejected", err)
	}
	if record, err := framing.EncodeRecord(body, FrameLimit); err != nil || uint64(len(record)) != FrameLimit {
		t.Fatal("request boundary differs from actual framing", err)
	}
	r.Config["value"] += "x"
	if _, err := EncodeRequest(r); !errors.Is(err, ErrConfiguration) {
		t.Fatal("request accepted body that exceeds cap with prefix")
	}
}

func TestNativeClientRejectsInvalidRequestBeforeDial(t *testing.T) {
	task := Task{Domain: "jobs.example.test", PostingID: "task", Config: map[string]string{}, Browser: true}
	for _, fault := range []string{"nil_config", "negative_due", "over_due", "invalid_id", "invalid_domain", "unknown_operation", "wrong_digest", "nonoperator_score", "oversized_frame"} {
		r := taskRequest(task, "prepare", "", true)
		switch fault {
		case "nil_config":
			r.Config = nil
		case "negative_due":
			r.NextScrapeAtMS = -1
		case "over_due":
			r.NextScrapeAtMS = maxInteger + 1
		case "invalid_id":
			r.PostingID = "secret input"
		case "invalid_domain":
			r.Domain = "https://secret.example"
		case "unknown_operation":
			r.Operation = "health"
		case "wrong_digest":
			r.Operation = "activate"
			r.ExpectedDigest = "secret"
		case "nonoperator_score":
			r.Operation = "enqueue"
			r.OperatorTransfer = false
			r.LegacyScheduleScore = "1"
		case "oversized_frame":
			r.Config = map[string]string{"value": strings.Repeat("secret", int(FrameLimit))}
		}
		if _, err := EncodeRequest(r); !errors.Is(err, ErrConfiguration) || strings.Contains(err.Error(), "secret") {
			t.Fatal("invalid native request accepted/exposed: " + fault)
		}
	}
}
