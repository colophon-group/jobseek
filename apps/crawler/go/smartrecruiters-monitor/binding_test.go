package smartrecruiters

import (
	"context"
	"errors"
	"net/http"
	"strings"
	"sync"
	"testing"
	"time"
)

func TestConcurrentCanonicalFailureRetainsObservedPublisherReservation(t *testing.T) {
	for _, source := range []string{"header", "meta"} {
		t.Run(source, func(t *testing.T) {
			var entered sync.WaitGroup
			entered.Add(2)
			client := doerFunc(func(r *http.Request) (*http.Response, error) {
				if strings.Contains(r.URL.String(), "?") {
					return response(r, 200, `{"content":[{"id":"1"},{"id":"2"}],"totalFound":2}`, nil), nil
				}
				entered.Done()
				entered.Wait()
				if strings.HasSuffix(r.URL.Path, "/1") {
					return response(r, 200, `{}`, nil), nil
				}
				// Model an already received response finishing after another worker's
				// invalid identity cancels the operation. Positive policy still wins.
				time.Sleep(10 * time.Millisecond)
				if source == "header" {
					return response(r, 404, "", http.Header{"Tdm-Reservation": {"1"}, "Tdm-Policy": {"https://policy.example/"}}), nil
				}
				return response(r, 200, `<meta name="tdm-reservation" content="1">`, nil), nil
			})
			result, err := fetchWith(context.Background(), "https://careers.smartrecruiters.com/fixture", Object{"canonical_identity": "job-v1"}, client, noPause)
			var reserved *Failure
			if !errors.As(err, &reserved) || reserved.Kind != "tdm" || reserved.Source != source || len(result.Jobs) != 0 {
				t.Fatal("concurrent parser failure erased observed publisher policy", result, err)
			}
		})
	}
}

func TestCanonicalIdentityBindingRefusesForeignTenantAndWrongMode(t *testing.T) {
	jobID := "01234567-89ab-4cde-8fab-0123456789ab"
	for _, o := range []Options{{Token: "fixture", Identity: "job-v1"}, {Token: "fixture", Identity: "job-location-v1"}} {
		id := "smartrecruiters:fixture:" + jobID
		if o.Identity == "job-location-v1" {
			id += "/geo/" + strings.Repeat("a", 64)
		}
		if !o.IdentityMatches(PostingURL(o.Token, "123"), id) {
			t.Fatal("exact canonical result rejected")
		}
		for _, bad := range []string{strings.Replace(id, "fixture", "other", 1), id + "/extra", "", strings.ToUpper(id)} {
			if o.IdentityMatches(PostingURL(o.Token, "123"), bad) {
				t.Fatal("foreign/malformed identity accepted")
			}
		}
		if o.IdentityMatches(PostingURL("other", "123"), id) {
			t.Fatal("foreign destination accepted")
		}
	}
}
