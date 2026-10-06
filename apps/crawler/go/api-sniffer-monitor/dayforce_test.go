package apisniffer

import (
	"encoding/json"
	"os"
	"reflect"
	"strings"
	"testing"
)

func TestDayforceIdentityBootstrapAndPagesMatchActualPython(t *testing.T) {
	raw, err := os.ReadFile("testdata/python_dayforce.json")
	if err != nil {
		t.Fatal(err)
	}
	var corpus []struct {
		Kind, Name      string
		Value, Expected any
		Error           bool
	}
	dec := json.NewDecoder(strings.NewReader(string(raw)))
	dec.UseNumber()
	if dec.Decode(&corpus) != nil || len(corpus) != 68 {
		t.Fatal("actual Python Dayforce corpus unavailable")
	}
	b := DayforceBoard{"fixture", "Careers"}
	site := DayforceSite{7, "en-US", []string{"en-US", "fr-CA"}, false}
	checked := 0
	for _, c := range corpus {
		if c.Kind == "field" {
			continue
		}
		checked++
		t.Run(c.Kind+"/"+c.Name, func(t *testing.T) {
			var got any
			var err error
			switch c.Kind {
			case "board":
				board, e := DayforceBoardFromURL(c.Value.(string))
				if c.Expected == nil {
					if e == nil {
						t.Fatal("invalid source admitted")
					}
					return
				}
				err = e
				got = map[string]any{"tenant": board.Tenant, "portal": board.Portal}
			case "site":
				value, e := DayforceExtractSite(c.Value.(string), b)
				err = e
				got = map[string]any{"job_board_id": value.JobBoardID, "culture": value.Culture, "cultures": value.Cultures, "disabled": value.Disabled}
			case "page":
				body, _ := json.Marshal(c.Value)
				d, e := Decode(body)
				if e != nil {
					t.Fatal(e)
				}
				total, rows, e := DayforcePage(d, b, site, 0)
				err = e
				got = []any{total, rows}
			case "overlap":
				body, _ := json.Marshal(c.Value)
				_, value, e := DayforceOptionsFromMetadata(b.ListingURL(), string(body))
				got, err = value, e
			case "body":
				offset, _ := c.Value.(json.Number).Int64()
				body, e := DayforceSearchBody(b, site, int(offset))
				err = e
				if e == nil {
					d, e := Decode(body)
					if e != nil {
						t.Fatal(e)
					}
					got = d.Value
				}
			default:
				t.Fatal("unknown frozen contract")
			}
			if c.Error {
				if err == nil {
					t.Fatal("invalid contract became success")
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			body, _ := json.Marshal(got)
			d, e := Decode(body)
			if e != nil {
				t.Fatal(e)
			}
			if !reflect.DeepEqual(d.Value, c.Expected) {
				t.Fatalf("actual=%s expected=%v", body, c.Expected)
			}
		})
	}
	if checked != 51 {
		t.Fatal("identity/page references lost", checked)
	}
}
