package apisniffer

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"os"
	"reflect"
	"slices"
	"sync"
	"sync/atomic"
	"testing"
)

func TestPapaCancelledInventoryDrainsOriginalFourPageBatch(t *testing.T) {
	var cases []struct {
		Name      string
		Responses map[string]string
	}
	b, err := os.ReadFile("testdata/python_last_papa_inventory.json")
	if err != nil || json.Unmarshal(b, &cases) != nil {
		t.Fatal("original full pagination fixture unavailable")
	}
	o, err := LastHTTPOptionsFromMetadata("papa_johns", "https://jobs.papajohns.com/jobs/", "{}")
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	var active, started, maximum atomic.Int32
	jobs, err := DiscoverPapaJohns(ctx, o, func(ctx context.Context, r Request) ([]byte, error) {
		if r.URL == o.ListingURL() {
			return []byte(cases[0].Responses[r.URL]), nil
		}
		n := active.Add(1)
		defer active.Add(-1)
		for old := maximum.Load(); n > old; old = maximum.Load() {
			if maximum.CompareAndSwap(old, n) {
				break
			}
		}
		if started.Add(1) == 4 {
			cancel()
		}
		<-ctx.Done()
		return nil, ctx.Err()
	})
	if !errors.Is(err, context.Canceled) || len(jobs) != 0 || active.Load() != 0 || maximum.Load() != 4 || started.Load() != 4 {
		t.Fatal("page work escaped the original batch or cancelled inventory attempt")
	}
}

func TestOriginalPapaCompletePaginationAndAtomicFailures(t *testing.T) {
	var cases []struct {
		Name           string
		Responses      map[string]string
		Requests, URLs []string
		Error          bool
	}
	b, err := os.ReadFile("testdata/python_last_papa_inventory.json")
	if err != nil || json.Unmarshal(b, &cases) != nil || len(cases) != 7 {
		t.Fatal("original Papa inventory corpus unavailable")
	}
	o, err := LastHTTPOptionsFromMetadata("papa_johns", "https://jobs.papajohns.com/jobs/", "{}")
	if err != nil {
		t.Fatal(err)
	}
	for _, c := range cases {
		t.Run(c.Name, func(t *testing.T) {
			var mu sync.Mutex
			requests := []string{}
			jobs, err := DiscoverPapaJohns(context.Background(), o, func(_ context.Context, r Request) ([]byte, error) {
				mu.Lock()
				defer mu.Unlock()
				requests = append(requests, r.URL)
				body, ok := c.Responses[r.URL]
				if !ok || r.Method != "GET" || r.Body != "" {
					return nil, fmt.Errorf("unmatched original pagination request")
				}
				return []byte(body), nil
			})
			slices.Sort(requests)
			if (err != nil) != c.Error || !reflect.DeepEqual(requests, c.Requests) || !c.Error && !reflect.DeepEqual(jobs, c.URLs) {
				t.Fatal("original complete pagination outcome changed", err)
			}
			if c.Error && len(jobs) != 0 {
				t.Fatal("failed inventory retained successful prefix")
			}
		})
	}
}

func TestOriginalUnisanteAuthoritativeListingAndVisibleDetails(t *testing.T) {
	raw, e := os.ReadFile("testdata/python_last_unisante.json")
	if e != nil {
		t.Fatal(e)
	}
	var corpus struct {
		Listings []struct {
			Name, Source, URL string
			Error             bool
			Jobs              map[string]UnisanteListingJob
		}
		Details []struct {
			Name, Source, Slug, Title, Today string
			Error                            bool
			Job                              map[string]any
			Normalizations                   []struct {
				Source      string
				Description *string
			}
		}
	}
	if json.Unmarshal(raw, &corpus) != nil || len(corpus.Listings) != 14 || len(corpus.Details) != 13 {
		t.Fatal("original corpus")
	}
	for _, c := range corpus.Listings {
		t.Run("listing/"+c.Name, func(t *testing.T) {
			got, e := ParseUnisanteListing(c.Source, c.URL)
			if (e != nil) != c.Error || !c.Error && !reflect.DeepEqual(got, c.Jobs) {
				t.Fatal("original authoritative listing changed", e)
			}
		})
	}
	for _, c := range corpus.Details {
		t.Run("detail/"+c.Name, func(t *testing.T) {
			used := 0
			j, e := ParseUnisanteDetail(c.Source, UnisanteListingJob{c.Slug, c.Title}, c.Today, func(source string) (*string, error) {
				if used >= len(c.Normalizations) {
					return nil, fmt.Errorf("unexpected normalization")
				}
				x := c.Normalizations[used]
				used++
				if source != x.Source {
					t.Fatal("original visible normalization input changed")
				}
				return x.Description, nil
			})
			if (e != nil) != c.Error {
				t.Fatal("original outcome changed", e, c.Error)
			}
			if used != len(c.Normalizations) {
				t.Fatal("original normalization calls changed", used, len(c.Normalizations))
			}
			if c.Error {
				if j != nil {
					t.Fatal("failed detail retained content")
				}
				return
			}
			if (j == nil) != (c.Job == nil) {
				t.Fatal("original expiration changed")
			}
			if j == nil {
				return
			}
			b, _ := json.Marshal(j)
			actual := map[string]any{}
			json.Unmarshal(b, &actual)
			actual["language"] = j.Extras["language"]
			for k, want := range c.Job {
				if !reflect.DeepEqual(actual[k], want) {
					t.Fatal("original field changed", k, actual[k], want)
				}
			}
		})
	}
}

func TestOriginalInforSessionFieldsAndBoundedFailure(t *testing.T) {
	raw, e := os.ReadFile("testdata/python_last_infor.json")
	if e != nil {
		t.Fatal(e)
	}
	var cases []struct {
		Name, Board string
		Error       bool
		Jobs        []map[string]any
		Exchanges   []struct {
			Method, URL, Body string
			Headers           map[string]string
			ResponseHeaders   map[string]string `json:"response_headers"`
		}
	}
	if json.Unmarshal(raw, &cases) != nil || len(cases) != 18 {
		t.Fatal("original corpus")
	}
	for _, c := range cases {
		t.Run(c.Name, func(t *testing.T) {
			o, e := LastHTTPOptionsFromMetadata("infor", c.Board, "{}")
			if e != nil {
				t.Fatal(e)
			}
			used := 0
			got, e := DiscoverInfor(context.Background(), o, func(ctx context.Context, r Request) ([]byte, http.Header, error) {
				if used >= len(c.Exchanges) {
					t.Fatal("unmatched request")
				}
				x := c.Exchanges[used]
				used++
				want, _ := url.Parse(x.URL)
				actual, _ := url.Parse(r.URL)
				if r.Method != x.Method || actual.Scheme != want.Scheme || actual.Host != want.Host || actual.Path != want.Path || !reflect.DeepEqual(actual.Query(), want.Query()) {
					t.Fatal("original request identity changed")
				}
				if used > 1 {
					for k, v := range x.Headers {
						if r.Headers.Get(k) != v {
							t.Fatal("original session header changed", k)
						}
					}
				}
				h := http.Header{}
				for k, v := range x.ResponseHeaders {
					h.Set(k, v)
				}
				return []byte(x.Body), h, nil
			})
			if (e != nil) != c.Error {
				t.Fatal("original outcome changed", e, c.Error)
			}
			if c.Error {
				if len(got) != 0 {
					t.Fatal("failed inventory retained partial rows")
				}
				return
			}
			if len(got) != len(c.Jobs) || used != len(c.Exchanges) {
				t.Fatal("original inventory/request count changed")
			}
			for n, j := range got {
				b, _ := json.Marshal(j)
				actual := map[string]any{}
				json.Unmarshal(b, &actual)
				actual["language"] = j.Extras["language"]
				for k, want := range c.Jobs[n] {
					if !reflect.DeepEqual(actual[k], want) {
						t.Fatal("original field changed", k, actual[k], want)
					}
				}
			}
		})
	}
}
func TestOriginalPapaJohnsListingProofs(t *testing.T) {
	raw, e := os.ReadFile("testdata/python_last_papa.json")
	if e != nil {
		t.Fatal(e)
	}
	var cases []struct {
		Name, Source string
		URLs         []string
		Total, Pages int
		Error        bool
	}
	if json.Unmarshal(raw, &cases) != nil || len(cases) != 10 {
		t.Fatal("original corpus")
	}
	for _, c := range cases {
		t.Run(c.Name, func(t *testing.T) {
			p, e := ParsePapaListing(c.Source, "https://jobs.papajohns.com/jobs/")
			if (e != nil) != c.Error {
				t.Fatal("original outcome changed", e)
			}
			if !c.Error && (!slices.Equal(p.URLs, c.URLs) || p.Total != c.Total || p.Pages != c.Pages) {
				t.Fatal("original listing proof changed", p)
			}
		})
	}
}

func TestOriginalPeopleSoftSessionContinuationAndFields(t *testing.T) {
	raw, e := os.ReadFile("testdata/python_last_peoplesoft.json")
	if e != nil {
		t.Fatal(e)
	}
	var cases []struct {
		Name, Board string
		Error       bool
		Jobs        []map[string]any
		Exchanges   []struct {
			Method, URL, Body string
			RequestBody       string `json:"request_body"`
			ContentType       string `json:"content_type"`
		}
	}
	if json.Unmarshal(raw, &cases) != nil || len(cases) != 20 {
		t.Fatal("original corpus")
	}
	for _, c := range cases {
		t.Run(c.Name, func(t *testing.T) {
			o, e := LastHTTPOptionsFromMetadata("peoplesoft", c.Board, "{}")
			if e != nil {
				t.Fatal(e)
			}
			used := 0
			got, e := DiscoverPeopleSoft(context.Background(), o, func(ctx context.Context, r Request) ([]byte, error) {
				if used >= len(c.Exchanges) {
					t.Fatal("unmatched request")
				}
				x := c.Exchanges[used]
				used++
				if r.Method != x.Method || r.URL != x.URL || r.Body != x.RequestBody || r.Headers.Get("Content-Type") != x.ContentType {
					t.Fatal("original ordered session/form request changed")
				}
				return []byte(x.Body), nil
			})
			if (e != nil) != c.Error || used != len(c.Exchanges) {
				t.Fatal("original outcome/request count changed", e, used, len(c.Exchanges))
			}
			if c.Error {
				if len(got) != 0 {
					t.Fatal("failed inventory retained partial rows")
				}
				return
			}
			if len(got) != len(c.Jobs) {
				t.Fatal("original inventory count changed")
			}
			for n, j := range got {
				b, _ := json.Marshal(j)
				actual := map[string]any{}
				json.Unmarshal(b, &actual)
				for k, want := range c.Jobs[n] {
					if !reflect.DeepEqual(actual[k], want) {
						t.Fatal("original field changed", k, actual[k], want)
					}
				}
			}
		})
	}
}
