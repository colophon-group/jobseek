package apisniffer

import (
	"encoding/json"
	"os"
	"reflect"
	"testing"
)

func TestItemListDocumentMatchesActualChromiumExpression(t *testing.T) {
	body, err := os.ReadFile("testdata/python_itemlist_browser.json")
	var corpus struct {
		Expression string
		Cases      []struct {
			Name, HTML    string
			BrowserResult json.RawMessage `json:"browser_result"`
			Error         bool
		}
	}
	if err != nil || json.Unmarshal(body, &corpus) != nil || len(corpus.Cases) != 18 || corpus.Expression != ItemListBrowserExpression {
		t.Fatal("actual browser expression corpus unavailable")
	}
	for _, c := range corpus.Cases {
		t.Run(c.Name, func(t *testing.T) {
			got, err := ParseItemListBrowserDocument(c.HTML)
			if c.Error {
				if err == nil {
					t.Fatal("invalid document became authoritative inventory")
				}
				return
			}
			want, failure := Decode(c.BrowserResult)
			if err != nil || failure != nil || !reflect.DeepEqual(got.Value, want.Value) {
				t.Fatal("browser transform differs", err)
			}
		})
	}
}
