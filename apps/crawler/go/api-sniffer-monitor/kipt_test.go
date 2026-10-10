package apisniffer

import (
	"encoding/json"
	"os"
	"reflect"
	"testing"
)

func TestKIPTOriginalBulletins(t *testing.T) {
	body, e := os.ReadFile("testdata/python_kipt.json")
	if e != nil {
		t.Fatal(e)
	}
	var fixture struct {
		Parser []struct {
			Name, URL, Text, Posted, Location string
			Jobs                              []json.RawMessage
		}
		Listing []struct {
			Name, Source, Base, Today string
			Age                       int
			Bulletins                 []KIPTBulletin
		}
	}
	if e = json.Unmarshal(body, &fixture); e != nil {
		t.Fatal(e)
	}
	for _, c := range fixture.Parser {
		t.Run(c.Name, func(t *testing.T) {
			jobs, e := ParseKIPTBulletin(c.URL, c.Text, c.Posted, c.Location)
			if e != nil {
				t.Fatal(e)
			}
			if len(jobs) != len(c.Jobs) {
				t.Fatalf("job count differs %d/%d", len(jobs), len(c.Jobs))
			}
			for i, j := range jobs {
				actual, _ := json.Marshal(j)
				var a, b map[string]any
				json.Unmarshal(actual, &a)
				json.Unmarshal(c.Jobs[i], &b)
				for k := range a {
					if !reflect.DeepEqual(a[k], b[k]) {
						t.Fatalf("field %s differs: %v vs %v", k, a[k], b[k])
					}
				}
			}
		})
	}
	for _, c := range fixture.Listing {
		t.Run(c.Name, func(t *testing.T) {
			got, e := ParseKIPTBulletins(c.Source, c.Base, c.Today, c.Age)
			if e != nil || !reflect.DeepEqual(got, c.Bulletins) {
				t.Fatalf("bulletins differ %v %v", got, e)
			}
		})
	}
}
