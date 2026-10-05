package worker

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"reflect"
	"testing"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func TestPhenomMatchesActualPythonLocalesShardsPolicyAndRequests(t *testing.T) {
	data, err := os.ReadFile("testdata/python_phenom.json")
	var corpus struct {
		Cases []struct {
			Name      string
			Resources map[string]struct {
				Body    string
				Status  int
				Headers map[string]string
			}
			Metadata map[string]any
			Requests []struct{ Method, URL string }
			Expected struct {
				Error bool
				URLs  []string
			}
		}
	}
	if err != nil || json.Unmarshal(data, &corpus) != nil || len(corpus.Cases) != 19 {
		t.Fatal("actual Python Phenom corpus missing")
	}
	for _, c := range corpus.Cases {
		t.Run(c.Name, func(t *testing.T) {
			requests := []struct{ Method, URL string }{}
			client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				requests = append(requests, struct{ Method, URL string }{r.Method, "https://" + r.Host + r.URL.String()})
				v, exists := c.Resources[r.URL.Path]
				if !exists {
					t.Error("unexpected shard", r.URL.Path)
					w.WriteHeader(404)
					return
				}
				for k, v := range v.Headers {
					w.Header().Set(k, v)
				}
				w.WriteHeader(v.Status)
				fmt.Fprint(w, v.Body)
			}))
			md, _ := json.Marshal(c.Metadata)
			config := map[string]string{"board_url": "https://example.com/careers", "metadata": string(md)}
			p := queue.GreenhouseMonitorProfile{Provider: "phenom", Profile: "phenom.sitemap-urls/v1", Endpoint: "https://example.com/sitemap.xml"}
			result, err := discoverPhenomInventory(context.Background(), client.client, p, config)
			if (err != nil) != c.Expected.Error || !reflect.DeepEqual(requests, c.Requests) {
				t.Fatal("Phenom failure or request semantics differ", err, requests, c.Requests)
			}
			if !c.Expected.Error {
				urls := []string{}
				for _, j := range result.Jobs {
					urls = append(urls, j.URL)
				}
				if !reflect.DeepEqual(urls, c.Expected.URLs) {
					t.Fatal("Phenom locale/canonical URL selection differs", urls, c.Expected.URLs)
				}
			}
		})
	}
}
