package apisniffer

import (
	"encoding/json"
	"os"
	"testing"
)

func TestFinalStaticActualOriginalFields(t *testing.T) {
	var corpus struct {
		Cases []struct {
			Provider, Name, Body string
			Content              map[string]any
		}
	}
	raw, e := os.ReadFile("testdata/python_final_static_detail.json")
	if e != nil || json.Unmarshal(raw, &corpus) != nil || len(corpus.Cases) != 40 {
		t.Fatal("actual corpus missing", e, len(corpus.Cases))
	}
	for _, c := range corpus.Cases {
		t.Run(c.Provider+"/"+c.Name, func(t *testing.T) {
			out, e := ParseStaticProviderDetail(c.Provider, c.Body)
			if e != nil {
				t.Fatal(e)
			}
			for key, want := range c.Content {
				a, _ := json.Marshal(out[key])
				b, _ := json.Marshal(want)
				if string(a) != string(b) {
					t.Errorf("%s differs: %s / %s", key, a, b)
				}
			}
		})
	}
}
