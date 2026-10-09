package apisniffer

import (
	"bytes"
	"encoding/json"
	"os"
	"reflect"
	"testing"
)

func TestFinalHTTPProvidersOriginalPythonCore(t *testing.T) {
	body, err := os.ReadFile("testdata/python_final_http_provider_core.json")
	if err != nil {
		t.Fatal(err)
	}
	var cases []struct {
		Name, Provider, Operation string
		Inputs, Expected          json.RawMessage
		Error                     bool
	}
	if err := json.Unmarshal(body, &cases); err != nil || len(cases) != 62 {
		t.Fatal("original corpus missing", len(cases), err)
	}
	for _, c := range cases {
		t.Run(c.Provider+"/"+c.Operation+"/"+c.Name, func(t *testing.T) {
			d, err := Decode(c.Inputs)
			if err != nil {
				t.Fatal(err)
			}
			inputs := d.Value.(map[string]any)
			var got any
			switch c.Provider + "/" + c.Operation {
			case "paynet/fields":
				got, err = PayNetJobFields(inputs["row"], inputs["company"].(string))
			case "paynet/board":
				if company, e := PayNetCompanyFromURL(inputs["url"].(string)); e == nil {
					got = company
				}
			case "nowhiring/fields":
				got, err = NowHiringJobFields(d, inputs["row"].(map[string]any), inputs["slug"].(string), inputs["customer"].(string))
			case "wecruit/fields":
				got, err = WecruitJobFields(d, inputs["origin"].(string), inputs["suite"].(string), inputs["listing"].(map[string]any), inputs["detail"].(map[string]any))
			case "fenbi/fields":
				got, err = FenbiJobFields(inputs["payload"].(map[string]any), inputs["kind"].(string), inputs["board"].(string))
			case "fenbi/literal":
				got, err = FenbiInventoryLiteral(inputs["bundle"].(string))
			case "nowhiring/criteria":
				got = NowHiringCriteria(d, inputs["criteria"])
			default:
				t.Fatal("unknown original case")
			}
			if c.Error {
				if err == nil {
					t.Fatal("original failure accepted")
				}
				return
			}
			if err != nil {
				t.Fatal("original accepted value rejected", err)
			}
			body, err := json.Marshal(got)
			if err != nil {
				t.Fatal(err)
			}
			decode := func(body []byte) any {
				var value any
				d := json.NewDecoder(bytes.NewReader(body))
				d.UseNumber()
				if err := d.Decode(&value); err != nil {
					t.Fatal(err)
				}
				return value
			}
			if !reflect.DeepEqual(decode(body), decode(c.Expected)) {
				t.Fatalf("original fields differ: got %s want %s", body, c.Expected)
			}
		})
	}
}
