package dom

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"os"
	"reflect"
	"strings"
	"testing"
)

func TestFrozenOriginalReadVariants(t *testing.T) {
	body, err := os.ReadFile("testdata/python_read_variants.json")
	if err != nil {
		t.Fatal(err)
	}
	var rows []struct {
		Name, Source string
		Config       Object
		Exchanges    []struct {
			URL, Body string
			Status    int
			Headers   map[string]string
		}
		Requested []string
		Expected  Object
		Error     *string
	}
	decoder := json.NewDecoder(strings.NewReader(string(body)))
	decoder.UseNumber()
	if decoder.Decode(&rows) != nil || len(rows) != 10 {
		t.Fatal("missing original reference contracts")
	}
	for _, row := range rows {
		t.Run(row.Name, func(t *testing.T) {
			requested := []string{}
			client := &http.Client{Transport: directRoundTrip(func(r *http.Request) (*http.Response, error) {
				requested = append(requested, r.URL.String())
				for _, x := range row.Exchanges {
					if x.URL == r.URL.String() {
						h := http.Header{}
						for k, v := range x.Headers {
							h.Set(k, v)
						}
						h.Set("Content-Type", "text/plain; charset=utf-8")
						return &http.Response{StatusCode: x.Status, Header: h, Body: io.NopCloser(strings.NewReader(x.Body)), Request: r}, nil
					}
				}
				t.Fatalf("unexpected request outside original reference: %s", r.URL.Hostname())
				return nil, nil
			})}
			got, err := FetchDetailWithClient(context.Background(), row.Source, row.Config, client)
			if !reflect.DeepEqual(requested, row.Requested) {
				t.Fatal("original request order changed", len(requested), len(row.Requested))
			}
			if row.Error != nil {
				if err == nil {
					t.Fatal("original failure became success")
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			for k, want := range row.Expected {
				a, _ := json.Marshal(got.Content[k])
				b, _ := json.Marshal(want)
				if string(a) != string(b) {
					t.Error("original field differs", k)
				}
			}
		})
	}
}
