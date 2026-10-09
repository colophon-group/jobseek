package apisniffer

import (
	"context"
	"encoding/json"
	"net/http"
	"os"
	"reflect"
	"regexp"
	"strings"
	"testing"
)

func TestSuccessFactorsLegacyOriginal(t *testing.T) {
	var corpus struct {
		Parsers []struct {
			Name, Body     string
			Batch          int
			Initial, Error bool
			Expected       json.RawMessage
		}
		Inventories []struct {
			Name  string
			Board struct {
				BoardURL string `json:"board_url"`
				Metadata map[string]any
			}
			Responses []struct {
				Body    string
				Headers map[string]string
			}
			Requests []struct {
				Method, URL, Body string
				Headers           map[string]string
			}
			Expected         json.RawMessage
			Truncated, Error bool
		}
	}
	raw, e := os.ReadFile("testdata/python_successfactors_legacy.json")
	if e != nil {
		t.Fatal(e)
	}
	if e = json.Unmarshal(raw, &corpus); e != nil {
		t.Fatal(e)
	}
	equal := func(t *testing.T, actual any, expected json.RawMessage) {
		t.Helper()
		var want, got any
		_ = json.Unmarshal(expected, &want)
		body, _ := json.Marshal(actual)
		_ = json.Unmarshal(body, &got)
		if !reflect.DeepEqual(got, want) {
			t.Fatalf("original output differs\nactual%s\nexpected%s", body, expected)
		}
	}
	for _, c := range corpus.Parsers {
		t.Run("parser/"+c.Name, func(t *testing.T) {
			out, e := ParseSuccessFactorsDWR(c.Body, c.Batch, c.Initial)
			if (e != nil) != c.Error {
				t.Fatalf("error%v expected refusal%v", e, c.Error)
			}
			if e == nil {
				equal(t, out, c.Expected)
			}
		})
	}
	nonce := regexp.MustCompile(`(?m)^scriptSessionId=[0-9a-f]{24}$`)
	for _, c := range corpus.Inventories {
		t.Run("inventory/"+c.Name, func(t *testing.T) {
			md, _ := json.Marshal(c.Board.Metadata)
			o, e := SuccessFactorsLegacyOptionsFromMetadata(c.Board.BoardURL, string(md))
			if e != nil {
				t.Fatal(e)
			}
			calls := 0
			out, e := DiscoverSuccessFactorsLegacy(context.Background(), o, func(_ context.Context, r Request) ([]byte, http.Header, error) {
				i := calls
				calls++
				if i >= len(c.Responses) || i >= len(c.Requests) {
					t.Fatal("unexpected request")
				}
				want := c.Requests[i]
				body := nonce.ReplaceAllString(r.Body, "scriptSessionId=0123456789abcdef01234567")
				if r.Method != want.Method || r.URL != want.URL || body != want.Body {
					t.Fatalf("request differs %s %s\n%s\nexpected%s", r.Method, r.URL, body, want.Body)
				}
				for k, v := range want.Headers {
					if strings.EqualFold(k, "content-type") && r.Method == "GET" {
						continue
					}
					if r.Headers.Get(k) != v {
						t.Fatalf("header%s differs", k)
					}
				}
				headers := http.Header{}
				for k, v := range c.Responses[i].Headers {
					headers.Set(k, v)
				}
				return []byte(c.Responses[i].Body), headers, nil
			})
			if (e != nil) != c.Error || calls != len(c.Requests) {
				t.Fatalf("error%v expected refusal%v calls%d expected%d", e, c.Error, calls, len(c.Requests))
			}
			if e == nil {
				equal(t, out.Jobs, c.Expected)
				if out.Truncated != c.Truncated {
					t.Fatal("truncation differs")
				}
			} else if len(out.Jobs) != 0 {
				t.Fatal("partial inventory escaped")
			}
		})
	}
}
func TestSuccessFactorsLegacyRejectsGraphCyclesAndBoundedInputs(t *testing.T) {
	for _, body := range []string{"//#DWR-REPLY\nvar s0={};s0.self=s0;dwr.engine._remoteHandleCallback('0','0',{payload:s0});", strings.Repeat("x", 5000001)} {
		if _, e := ParseSuccessFactorsDWR(body, 0, true); e == nil {
			t.Fatal("unbounded graph accepted")
		}
	}
	board := "https://career5.successfactors.eu/career?company=Acme"
	for _, md := range []string{`{"preset":"successfactors","variant":"legacy","company":"Foreign","host":"career5.successfactors.eu"}`, `{"preset":"successfactors","variant":"legacy","listing_url":"https://evil.test/career?company=Acme"}`} {
		if _, e := SuccessFactorsLegacyOptionsFromMetadata(board, md); e == nil {
			t.Fatal("foreign tenant config")
		}
	}
	o, e := SuccessFactorsLegacyOptionsFromMetadata(board, `{"preset":"successfactors","variant":"legacy"}`)
	if e != nil {
		t.Fatal(e)
	}
	if o.ResourceMatches(o.DWRURL("search")+"?company=foreign") || o.ResourceMatches("https://career5.successfactors.eu/xi/ajax/remoting/call/plaincall/Other.search.dwr") {
		t.Fatal("foreign resource")
	}
}
