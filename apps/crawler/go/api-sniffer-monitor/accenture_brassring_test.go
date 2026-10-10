package apisniffer

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"os"
	"reflect"
	"regexp"
	"strconv"
	"strings"
	"testing"
)

func TestOriginalAccentureBrassRingProjection(t *testing.T) {
	var corpus struct {
		Fields []struct {
			Provider, Endpoint, Name string
			Row, Expected            json.RawMessage
		}
		Identities []struct {
			URL      string
			Expected []string
		}
		Normalizations map[string]*string
		Details        []struct {
			Body     string
			Expected []string
		}
		Requests []struct {
			Country, Language, Site, Body string
			Offset                        int
		}
	}
	b, e := os.ReadFile("testdata/python_accenture_brassring.json")
	if e != nil || json.Unmarshal(b, &corpus) != nil {
		t.Fatal("original corpus unavailable")
	}
	for _, c := range corpus.Fields {
		t.Run(c.Provider+"/"+c.Endpoint+"/"+c.Name, func(t *testing.T) {
			d, e := Decode(c.Row)
			if e != nil {
				t.Fatal(e)
			}
			var j Job
			var valid bool
			if c.Provider == "accenture" {
				j, valid, e = AccentureJob(d.Value.(map[string]any), AccentureOptions{Site: "us-en", Endpoint: c.Endpoint})
			} else {
				j, valid, e = BrassRingJob(d.Value, BrassRingBoard{"25416", "5998"}, func(s string) (*string, error) {
					v, ok := corpus.Normalizations[s]
					if !ok {
						t.Fatal("unqualified HTML")
					}
					return v, nil
				})
			}
			if e != nil {
				t.Fatal(e)
			}
			if string(c.Expected) == "null" {
				if valid {
					t.Fatal("invalid original admitted")
				}
				return
			}
			if !valid {
				t.Fatal("valid original refused")
			}
			b, _ := json.Marshal(j)
			var actual, expected map[string]any
			json.Unmarshal(b, &actual)
			json.Unmarshal(c.Expected, &expected)
			for k, v := range actual {
				if v == nil {
					delete(actual, k)
				}
			}
			if !reflect.DeepEqual(actual, expected) {
				t.Fatalf("original fields differ: actual%s expected%s", b, c.Expected)
			}
		})
	}
	for _, c := range corpus.Identities {
		b, e := BrassRingBoardFromURL(c.URL)
		if c.Expected == nil {
			if e == nil {
				t.Fatal("invalid identity admitted")
			}
		} else if e != nil || !reflect.DeepEqual([]string{b.PartnerID, b.SiteID}, c.Expected) {
			t.Fatal("original identity differs")
		}
	}
	for _, c := range corpus.Requests {
		r := (AccentureOptions{Country: c.Country, Language: c.Language, Site: c.Site, Endpoint: AccentureFindJobs}).FindJobsRequest(c.Offset, nil)
		if r.Body != c.Body {
			t.Fatal("original multipart request differs")
		}
	}
	for _, c := range corpus.Details {
		actual, e := BrassRingDetailLocation(c.Body, "3383626")
		if c.Expected == nil {
			if e == nil {
				t.Fatal("unsafe original detail admitted")
			}
		} else if e != nil || !reflect.DeepEqual(actual, c.Expected) {
			t.Fatal("original detail location differs", e)
		}
	}
}

func TestOriginalAccentureCapturedBodiesAndInventoryPagination(t *testing.T) {
	var corpus struct {
		Captured    []struct{ Body, Expected string } `json:"captured_requests"`
		Inventories []struct {
			Endpoint                 string
			Actual, Advertised, Rows int
			Capped                   bool
			Offsets                  []int
			IDsSHA256                string `json:"ids_sha256"`
		}
	}
	b, e := os.ReadFile("testdata/python_accenture_brassring.json")
	if e != nil || json.Unmarshal(b, &corpus) != nil {
		t.Fatal("original inventory corpus unavailable")
	}
	for _, c := range corpus.Captured {
		r, e := AccentureCapturedRequest(Request{Method: "POST", URL: "https://www.accenture.com/api/accenture/" + AccentureJobSearch, Body: c.Body}, 1000)
		if e != nil || r.Body != c.Expected {
			t.Fatal("original captured body differs", e, r.Body, c.Expected)
		}
	}
	offsetRE := regexp.MustCompile("name=\"startIndex\"\r\n\r\n([0-9]+)")
	for _, c := range corpus.Inventories {
		t.Run(c.Endpoint+"/"+strconv.Itoa(c.Actual), func(t *testing.T) {
			calls := []int{}
			o := AccentureOptions{Country: "USA", Language: "en", Site: "us-en", Endpoint: c.Endpoint}
			var captured *Request
			if c.Endpoint == AccentureJobSearch {
				captured = &Request{Method: "POST", URL: "https://www.accenture.com/api/accenture/" + c.Endpoint, Body: `{"startIndex":0,"maxResultSize":20}`}
			}
			inv, e := DiscoverAccenture(context.Background(), o, func(_ context.Context, r Request) (*Document, error) {
				offset := 0
				if strings.HasPrefix(r.Body, "----") {
					m := offsetRE.FindStringSubmatch(r.Body)
					if len(m) != 2 {
						t.Fatal("original page offset missing")
					}
					offset, _ = strconv.Atoi(m[1])
				} else {
					var b map[string]any
					if json.Unmarshal([]byte(r.Body), &b) != nil {
						t.Fatal("captured body invalid")
					}
					offset = int(b["startIndex"].(float64))
				}
				calls = append(calls, offset)
				rows := []any{}
				for i := offset + 1; i <= min(offset+500, c.Actual); i++ {
					rows = append(rows, map[string]any{"guid": strconv.Itoa(i), "jobDetailUrl": "https://www.accenture.com/fr-fr/careers/jobdetails?id=" + strconv.Itoa(i), "title": "Engineer", "jobDescription": "Build", "location": "Zurich", "jobCityState": "Paris"})
				}
				body, _ := json.Marshal(map[string]any{"totalHits": map[string]any{"total": c.Advertised}, "data": rows})
				return Decode(body)
			}, captured, nil)
			if e != nil || len(inv.Jobs) != c.Rows || inv.Truncated != c.Capped || !reflect.DeepEqual(calls, c.Offsets) {
				t.Fatal("original inventory/count/requests differ", e, len(inv.Jobs), inv.Truncated)
			}
			ids := []string{}
			for _, j := range inv.Jobs {
				_, id, _ := strings.Cut(j.URL, "?id=")
				ids = append(ids, id)
			}
			digest := sha256.Sum256([]byte(strings.Join(ids, "\n")))
			if fmt.Sprintf("%x", digest) != c.IDsSHA256 {
				t.Fatal("original full inventory identities differ")
			}
		})
	}
}

func TestAccentureIncompleteAdvertisedInventoryHasNoPublishedPrefix(t *testing.T) {
	calls := 0
	emitted := false
	inv, e := DiscoverAccenture(context.Background(), AccentureOptions{Country: "USA", Language: "en", Site: "us-en", Endpoint: AccentureFindJobs}, func(_ context.Context, _ Request) (*Document, error) {
		calls++
		rows := []any{}
		if calls == 1 {
			for i := 0; i < 500; i++ {
				rows = append(rows, map[string]any{"guid": strconv.Itoa(i + 1)})
			}
		}
		b, _ := json.Marshal(map[string]any{"data": rows, "totalHits": 501})
		return Decode(b)
	}, nil, func([]Job) error { emitted = true; return nil })
	if e == nil || emitted || len(inv.Jobs) != 0 {
		t.Fatal("incomplete original page sequence obtained publication authority")
	}
}
