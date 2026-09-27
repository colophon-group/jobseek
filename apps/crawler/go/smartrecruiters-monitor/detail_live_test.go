package smartrecruiters

import (
	"context"
	"encoding/json"
	"net/http"
	"os"
	"reflect"
	"testing"
)

func TestPythonDetailParity(t *testing.T) {
	body, err := os.ReadFile("testdata/python_detail.json")
	if err != nil {
		t.Fatal(err)
	}
	var fixtures []struct {
		Name     string
		Input    json.RawMessage
		Expected Object
		Error    string
	}
	if err = json.Unmarshal(body, &fixtures); err != nil {
		t.Fatal(err)
	}
	for _, f := range fixtures {
		t.Run(f.Name, func(t *testing.T) {
			result, err := fetchDetailWith(context.Background(), "https://jobs.smartrecruiters.com/Acme/123-slug", doerFunc(func(r *http.Request) (*http.Response, error) {
				if r.URL.String() != ListURL("Acme")+"/123-slug" {
					t.Fatal(r.URL)
				}
				return response(r, 200, string(f.Input), nil), nil
			}))
			if f.Error != "" {
				if err == nil {
					t.Fatal("expected failure")
				}
				return
			}
			if err != nil || result.Requests != 1 || result.Responses != 1 {
				t.Fatalf("%+v %v", result, err)
			}
			// The content dataclass supplies these optional defaults at the bridge.
			actual := result.Content
			actual["language"], actual["extras"] = nil, nil
			encoded, _ := json.Marshal(actual)
			var decoded Object
			if err = json.Unmarshal(encoded, &decoded); err != nil {
				t.Fatal(err)
			}
			if !reflect.DeepEqual(decoded, f.Expected) {
				t.Fatalf("Go=%s Python=%v", encoded, f.Expected)
			}
		})
	}
}

func TestDetailSingleRequestAndURLShapes(t *testing.T) {
	for _, path := range []string{"/Acme/123-slug", "/oneclick-ui/company/Acme/publication/123-slug", "/oneclick-ui/company/Acme/job/123-slug"} {
		for _, status := range []int{301, 404, 429, 500} {
			result, err := fetchDetailWith(context.Background(), "https://jobs.smartrecruiters.com"+path+"?source=test", doerFunc(func(r *http.Request) (*http.Response, error) {
				return response(r, status, "", nil), nil
			}))
			if err != nil || result.Content != nil || result.Requests != 1 || result.Responses != 1 || result.Status != status {
				t.Fatalf("%+v %v", result, err)
			}
		}
	}
	for _, raw := range []string{"https://evil.test/Acme/1", "https://jobs.smartrecruiters.com@evil.test/Acme/1", "http://jobs.smartrecruiters.com/Acme/1", "https://jobs.smartrecruiters.com/Acme/1/extra"} {
		_, _, err := DetailEndpoint(raw)
		if err == nil {
			t.Fatalf("accepted %s", raw)
		}
	}
}
