package apisniffer

import (
	"bytes"
	"context"
	"encoding/json"
	"os"
	"reflect"
	"sort"
	"testing"
)

func TestOriginalInitialResponseDecryption(t *testing.T) {
	raw, err := os.ReadFile("testdata/python_response_decrypt.json")
	var cases []struct {
		Name            string
		Input, Expected json.RawMessage
		Config          map[string]any
	}
	if err != nil || json.Unmarshal(raw, &cases) != nil || len(cases) != 15 {
		t.Fatal("original decrypt oracle unavailable")
	}
	for _, c := range cases {
		t.Run(c.Name, func(t *testing.T) {
			d, err := Decode(c.Input)
			if err != nil {
				t.Fatal(err)
			}
			options, err := parseResponseDecrypt(c.Config)
			if err != nil {
				t.Fatal("original supported decrypt options rejected")
			}
			got := decryptInitialResponse(d, options)
			want, err := Decode(c.Expected)
			if err != nil || !reflect.DeepEqual(got.Value, want.Value) {
				t.Fatal("original initial response projection changed")
			}
			unchanged, err := Decode(c.Input)
			if err != nil || !reflect.DeepEqual(d.Value, unchanged.Value) {
				t.Fatal("decryption mutated captured provenance")
			}
			if c.Name == "suffix" {
				root := got.Value.(map[string]any)
				inside := root["Data"].(map[string]any)["ordered"].(map[string]any)
				if !reflect.DeepEqual(got.ObjectKeys(root), []string{"before", "Data", "after"}) || !reflect.DeepEqual(got.ObjectKeys(inside), []string{"z", "a"}) {
					t.Fatal("original object order lost")
				}
			}
		})
	}
	for _, raw := range []any{"private", map[string]any{"key": "short"}, map[string]any{"key": "fixture-key-1234", "iv_mode": "unknown"}, map[string]any{"key": "fixture-key-1234", "unknown": true}} {
		if _, err := parseResponseDecrypt(raw); err == nil {
			t.Fatal("unqualified decrypt shape admitted")
		}
	}
}

func TestResponseDecryptActualPublicOriginalFields(t *testing.T) {
	path := os.Getenv("JOBSEEK_API_DECRYPT_PUBLIC_CAPTURE")
	if path == "" {
		t.Skip("requires private original public capture with original optional decoder")
	}
	raw, err := os.ReadFile(path)
	var original struct {
		Status string
		Board  struct {
			URL      string `json:"board_url"`
			Metadata json.RawMessage
		}
		Jobs      []Job
		Exchanges []struct {
			Method, URL, Body string
			RequestBody       string `json:"request_body"`
			Status            int
		}
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	if err != nil || decoder.Decode(&original) != nil || original.Status != "complete" || len(original.Jobs) != 18 || len(original.Exchanges) != 1 {
		t.Fatal("original public encrypted inventory unavailable")
	}
	o, err := OptionsFromMetadata(original.Board.URL, string(original.Board.Metadata))
	if err != nil {
		t.Fatal("canonical encrypted configuration rejected")
	}
	calls := 0
	got, err := Discover(context.Background(), o, func(_ context.Context, r Request) (*Document, error) {
		calls++
		x := original.Exchanges[0]
		if calls != 1 || r.URL != x.URL || r.Method != x.Method || r.Body != x.RequestBody {
			t.Fatal("original encrypted inventory request changed")
		}
		return Decode([]byte(x.Body))
	}, func(base, reference string) (string, error) { return reference, nil })
	for i := range original.Jobs {
		if original.Jobs[i].Metadata == nil {
			original.Jobs[i].Metadata = map[string]any{}
		}
		if original.Jobs[i].Extras == nil {
			original.Jobs[i].Extras = map[string]any{}
		}
	}
	sort.Slice(original.Jobs, func(i, j int) bool { return original.Jobs[i].URL < original.Jobs[j].URL })
	sort.Slice(got.Jobs, func(i, j int) bool { return got.Jobs[i].URL < got.Jobs[j].URL })
	if err != nil || got.Truncated || calls != 1 || !reflect.DeepEqual(got.Jobs, original.Jobs) {
		if len(got.Jobs) == len(original.Jobs) {
			for i := range got.Jobs {
				a, b := reflect.ValueOf(got.Jobs[i]), reflect.ValueOf(original.Jobs[i])
				for j := 0; j < a.NumField(); j++ {
					if !reflect.DeepEqual(a.Field(j).Interface(), b.Field(j).Interface()) {
						t.Log("original field differs", "index", i, "field", a.Type().Field(j).Name)
					}
				}
			}
		}
		t.Fatal("original encrypted inventory fields differ", "native_count", len(got.Jobs), "original_count", len(original.Jobs))
	}
	t.Log("18 original public jobs; all original fields and request match; no optional Python decoder needed")
}
