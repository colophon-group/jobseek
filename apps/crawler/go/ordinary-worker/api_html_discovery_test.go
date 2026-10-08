package worker

import (
	"context"
	"io"
	"net/http"
	"strings"
	"testing"
)

func TestHTMLAPIUnicodeAndPublisherReservation(t *testing.T) {
	for _, reserved := range []bool{false, true} {
		p, c := configuredAPIFixture(`{"api_url":"https://example.com/api","json_path":"html","pagination":{"param_name":"page","max_pages":2}}`)
		calls := 0
		client := &http.Client{Transport: workdayDetailRoundTrip(func(r *http.Request) (*http.Response, error) {
			calls++
			body := `{"html":"<a href='/jobs/ü-1%zz'>one</a>"}`
			h := http.Header{}
			if calls == 2 {
				body = `{"html":""}`
				if reserved {
					h.Set("TDM-Reservation", "1")
				}
			}
			return &http.Response{StatusCode: 200, Header: h, Body: io.NopCloser(strings.NewReader(body)), Request: r}, nil
		})}
		got, err := discoverAPISnifferInventory(context.Background(), client, p, c)
		if calls != 2 {
			t.Fatal("HTML request traversal changed", calls)
		}
		if reserved {
			if err == nil || got.Response == nil || !got.Response.reserved || len(got.Jobs) != 0 {
				t.Fatal("later publisher policy lost or partial inventory retained", got, err)
			}
		} else if err != nil || len(got.Jobs) != 1 || !got.Jobs[0].URLOnly || got.Jobs[0].URL != "https://example.com/jobs/ü-1%zz" {
			t.Fatal("Python URL-only joining changed", got, err)
		}
	}
}
