package worker

import (
	"encoding/json"
	"fmt"
	"os"
	"reflect"
	"strconv"
	"testing"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	enrichment "github.com/colophon-group/jobseek/apps/crawler/go/job-enrichment"
)

func TestJarviAndJob51OriginalPythonCore(t *testing.T) {
	body, e := os.ReadFile("../api-sniffer-monitor/testdata/python_jarvi_job51_core.json")
	if e != nil {
		t.Fatal(e)
	}
	var corpus []struct {
		Name, Provider, Operation string
		Inputs                    json.RawMessage
		Expected                  any
		Error                     bool
	}
	if json.Unmarshal(body, &corpus) != nil || len(corpus) != 33 {
		t.Fatal("original corpus unavailable")
	}
	for i, c := range corpus {
		t.Run(fmt.Sprintf("%s/%s/%s/%d", c.Provider, c.Operation, c.Name, i), func(t *testing.T) {
			d, e := api.Decode(c.Inputs)
			if e != nil {
				t.Fatal(e)
			}
			input := d.Value.(map[string]any)
			var got any
			var err error
			switch c.Operation {
			case "fields":
				if c.Provider == "jarvi" {
					got, err = api.JarviJobFields(input["row"].(map[string]any), input["board"].(string), input["currency"].(string))
				} else {
					ctmid, _ := strconv.ParseInt(fmt.Sprint(input["ctmid"]), 10, 64)
					got, err = api.Job51JobFields(input["row"].(map[string]any), ctmid, input["id"].(string), enrichment.NormalizeDescriptionHTML)
				}
			case "embed":
				got, err = api.JarviEmbed(input["page"].(string))
				if c.Expected == nil {
					err = nil
				}
			case "list-request":
				ctmid, _ := strconv.ParseInt(fmt.Sprint(input["ctmid"]), 10, 64)
				page, _ := strconv.Atoi(fmt.Sprint(input["page"]))
				request, e := api.Job51ListRequest(ctmid, page)
				got, err = request.URL, e
			case "detail-request":
				request, e := api.Job51DetailRequest(input["id"].(string))
				got, err = request.URL, e
			case "jsonp":
				got, err = api.Job51JSONP([]byte(input["body"].(string)))
			default:
				t.Fatal("unknown original operation")
			}
			if c.Error {
				if err == nil {
					t.Fatal("invalid original input accepted")
				}
				return
			}
			if err != nil {
				t.Fatal("original valid input rejected", err)
			}
			encoded, e := json.Marshal(got)
			if e != nil {
				t.Fatal(e)
			}
			var normalized any
			if json.Unmarshal(encoded, &normalized) != nil {
				t.Fatal("invalid canonical output")
			}
			if !reflect.DeepEqual(normalized, c.Expected) {
				t.Fatalf("original fields/protocol differ\ngot:%s\nwant:%s", encoded, mustProviderJSON(c.Expected))
			}
		})
	}
}
func mustProviderJSON(v any) string { body, _ := json.Marshal(v); return string(body) }
