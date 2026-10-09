package apisniffer

import (
	"bytes"
	"encoding/json"
	"os"
	"reflect"
	"testing"
)

func TestCuratelyInploiJobConvoOriginalFields(t *testing.T) {
	b, e := os.ReadFile("testdata/python_curately_inploi_jobconvo_fields.json")
	if e != nil {
		t.Fatal(e)
	}
	var cases []struct {
		Provider, Mode string
		Row, Expected  json.RawMessage
		Error          bool
	}
	if json.Unmarshal(b, &cases) != nil || len(cases) != 119 {
		t.Fatal("original fields unavailable")
	}
	for _, c := range cases {
		t.Run(c.Provider+"/"+c.Mode, func(t *testing.T) {
			d, e := Decode(c.Row)
			if e != nil {
				t.Fatal(e)
			}
			row := d.Value.(map[string]any)
			var got map[string]any
			switch c.Provider {
			case "curately":
				got, e = CuratelyJobFields(row, "example", "usd", "hour", "en")
			case "inploi":
				got, e = InploiJobFields(d, row, "https://careers.example.com/search", "")
			case "jobconvo":
				got = JobConvoDetailFields(row)
			default:
				t.Fatal("unknown original provider")
			}
			if c.Error {
				if e == nil {
					t.Fatal("original failure accepted")
				}
				return
			}
			if e != nil {
				t.Fatal(e)
			}
			normalize := func(b []byte) any {
				var v any
				decoder := json.NewDecoder(bytes.NewReader(b))
				decoder.UseNumber()
				if decoder.Decode(&v) != nil {
					t.Fatal("invalid comparison")
				}
				return v
			}
			body, e := json.Marshal(got)
			if e != nil {
				t.Fatal(e)
			}
			if !reflect.DeepEqual(normalize(body), normalize(c.Expected)) {
				t.Fatalf("original fields differ: got %s want %s", body, c.Expected)
			}
		})
	}
}
