package apisniffer

import (
	"context"
	"encoding/json"
	"os"
	"reflect"
	"sort"
	"testing"
)

func TestUmantisCompleteDiscoveryAndOwnerProofMatchActualPython(t *testing.T) {
	var corpus struct {
		Owners []struct {
			Name, Body string
			Valid      bool
		}
		Visible []struct {
			Name, Body, Expected string
			Valid                bool
		}
		Discovery []struct {
			Name, Listing string
			Metadata      map[string]any
			Responses     map[string]struct {
				Status int
				Body   string
			}
			URLs, Calls []string
			Error       bool
		}
	}
	raw, err := os.ReadFile("testdata/python_umantis.json")
	if err != nil || json.Unmarshal(raw, &corpus) != nil || len(corpus.Owners) != 10 || len(corpus.Visible) != 10 || len(corpus.Discovery) != 10 {
		t.Fatal("missing frozen Python proof cases")
	}
	for _, c := range corpus.Owners {
		t.Run("owner/"+c.Name, func(t *testing.T) {
			if (ValidateUmantisDetailOwner(c.Body, "Université de Neuchâtel") == nil) != c.Valid {
				t.Fatal(c)
			}
		})
	}
	for _, c := range corpus.Visible {
		t.Run("empty/"+c.Name, func(t *testing.T) {
			if umantisVisibleEmpty(c.Body, c.Expected) != c.Valid {
				t.Fatal(c)
			}
		})
	}
	for _, c := range corpus.Discovery {
		t.Run("discovery/"+c.Name, func(t *testing.T) {
			metadata, _ := json.Marshal(c.Metadata)
			o, err := UmantisOptionsFromMetadata(c.Listing, string(metadata))
			if err != nil {
				t.Fatal(err)
			}
			calls := []string{}
			rows, truncated, err := DiscoverUmantis(context.Background(), o, func(_ context.Context, resource string, tail bool) ([]byte, string, error) {
				calls = append(calls, resource)
				response, found := c.Responses[resource]
				if !found {
					t.Fatal("unexpected resource", resource)
				}
				if response.Status == 404 || response.Status == 410 {
					if !tail {
						return nil, resource, ErrInventory
					}
					return nil, resource, nil
				}
				return []byte(response.Body), resource, nil
			})
			if (err != nil) != c.Error || truncated {
				t.Fatal(rows, truncated, err, c.Error)
			}
			if err != nil && len(rows) != 0 {
				t.Fatal("failed discovery exposed successful prefix", rows)
			}
			if !reflect.DeepEqual(calls, c.Calls) {
				t.Fatal(calls, c.Calls)
			}
			if !c.Error {
				urls := []string{}
				for _, r := range rows {
					urls = append(urls, r.URL)
				}
				sort.Strings(urls)
				if !reflect.DeepEqual(urls, c.URLs) {
					t.Fatal(urls, c.URLs)
				}
			}
		})
	}
}

func TestUmantisReservationCancellationAndRootRedirect(t *testing.T) {
	o, _ := UmantisOptionsFromMetadata("https://recruitingapp-3040.umantis.com/Jobs/All", "{}")
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if rows, _, err := DiscoverUmantis(ctx, o, func(context.Context, string, bool) ([]byte, string, error) {
		t.Fatal("cancelled operation fetched")
		return nil, "", nil
	}); err != context.Canceled || len(rows) != 0 {
		t.Fatal(rows, err)
	}
	for _, final := range []string{"https://foreign.example/Jobs/All", "https://recruitingapp-3040.umantis.com/Other"} {
		if rows, _, err := DiscoverUmantis(context.Background(), o, func(context.Context, string, bool) ([]byte, string, error) { return []byte("empty"), final, nil }); err == nil || len(rows) != 0 {
			t.Fatal(rows, err)
		}
	}
	reservation := ErrField
	if rows, _, err := DiscoverUmantis(context.Background(), o, func(context.Context, string, bool) ([]byte, string, error) { return nil, "", reservation }); err != reservation || len(rows) != 0 {
		t.Fatal(rows, err)
	}
}
