package smartrecruiters

import (
	"context"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"testing"
)

const captureTestBoard = "96ca9888-ad6d-491b-951b-bcb170c6a117"

func TestDetailCaptureRetainsSinglePolicyCheckedResponse(t *testing.T) {
	t.Setenv("SMARTRECRUITERS_GO_DETAIL_BOARD_IDS", captureTestBoard)
	dir := t.TempDir()
	raw := "https://jobs.smartrecruiters.com/Acme/123"
	body := "{\n  \"name\": \"Engineer\", \"jobAd\": {}\n}"
	result, err := fetchDetailRetained(context.Background(), raw, doerFunc(func(r *http.Request) (*http.Response, error) {
		return response(r, 200, body, nil), nil
	}), detailCapture(captureTestBoard, raw, dir))
	if err != nil || result.Requests != 1 || result.Responses != 1 || result.Content["title"] != "Engineer" {
		t.Fatalf("%+v %v", result, err)
	}
	path := filepath.Join(dir, "jobseek-smartrecruiters-go-detail-"+captureTestBoard+"-1.json")
	info, err := os.Stat(path)
	if err != nil || info.Mode().Perm() != 0600 {
		t.Fatalf("private capture: %v %v", info, err)
	}
	stored, _ := os.ReadFile(path)
	var envelope map[string]string
	if err = json.Unmarshal(stored, &envelope); err != nil {
		t.Fatal(err)
	}
	decoded, err := base64.StdEncoding.DecodeString(envelope["body_base64"])
	if err != nil || string(decoded) != body || envelope["body_sha256"] != fmt.Sprintf("%x", sha256.Sum256([]byte(body))) || envelope["url"] != raw || envelope["endpoint"] != ListURL("Acme")+"/123" || envelope["board_id"] != captureTestBoard {
		t.Fatalf("capture changed input: %v", envelope)
	}
	for _, tc := range []struct {
		status  int
		body    string
		headers http.Header
	}{
		{200, body, http.Header{"Tdm-Reservation": {"1"}}},
		{200, "not JSON", nil},
		{404, body, nil},
	} {
		emptyDir := t.TempDir()
		_, _ = fetchDetailRetained(context.Background(), raw, doerFunc(func(r *http.Request) (*http.Response, error) {
			return response(r, tc.status, tc.body, tc.headers), nil
		}), detailCapture(captureTestBoard, raw, emptyDir))
		entries, _ := os.ReadDir(emptyDir)
		if len(entries) != 0 {
			t.Fatal("retained a denied or invalid response")
		}
	}
}

func TestDetailCaptureSelectionAndBound(t *testing.T) {
	t.Setenv("SMARTRECRUITERS_GO_DETAIL_BOARD_IDS", "")
	if detailCapture(captureTestBoard, "unused", t.TempDir()) != nil {
		t.Fatal("percentage route must not capture implicitly")
	}
	t.Setenv("SMARTRECRUITERS_GO_DETAIL_BOARD_IDS", captureTestBoard)
	if detailCapture("../../bad", "unused", t.TempDir()) != nil {
		t.Fatal("invalid board ID")
	}
	dir := t.TempDir()
	for i := 0; i < 6; i++ {
		raw := fmt.Sprintf("https://jobs.smartrecruiters.com/Acme/%d", i)
		retain := detailCapture(captureTestBoard, raw, dir)
		retain(ListURL("Acme"), []byte(`{"name":"first"}`))
		retain(ListURL("Acme"), []byte(`{"name":"changed"}`))
	}
	entries, _ := os.ReadDir(dir)
	if len(entries) != 4 {
		t.Fatal("capture exceeded four jobs", len(entries))
	}
	for _, entry := range entries {
		stored, _ := os.ReadFile(filepath.Join(dir, entry.Name()))
		var envelope map[string]string
		_ = json.Unmarshal(stored, &envelope)
		decoded, _ := base64.StdEncoding.DecodeString(envelope["body_base64"])
		if string(decoded) != `{"name":"first"}` {
			t.Fatal("overwrote retained bytes")
		}
	}
}
