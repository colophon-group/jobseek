package apisniffer

import (
	"encoding/json"
	"os"
	"reflect"
	"testing"
)

func TestYumChinaLiteralMatchesOriginalChromiumAndPython(t *testing.T) {
	body, err := os.ReadFile("testdata/python_yum_china_browser.json")
	var corpus struct {
		Cases []struct {
			Name, Script  string
			BrowserResult json.RawMessage `json:"browser_result"`
		}
	}
	if err != nil || json.Unmarshal(body, &corpus) != nil || len(corpus.Cases) != 5 {
		t.Fatal("original browser corpus missing")
	}
	for _, c := range corpus.Cases {
		t.Run(c.Name, func(t *testing.T) {
			got, err := ParseYumChinaJobs(c.Script)
			want, referenceErr := Decode(c.BrowserResult)
			if err != nil || referenceErr != nil || !reflect.DeepEqual(got.Value, want.Value) {
				t.Fatal("original browser variable differs", err)
			}
		})
	}
}

func TestYumChinaMissingOrChangedAssetCannotProveAbsence(t *testing.T) {
	row := `{type:"Track",title:"Engineer",desc:"<p>Build systems</p>",link:"https://example.test/jobs/42"}`
	for _, source := range []string{
		"", "let jobList = null;", "let jobList = [",
		"let jobList = [" + row + ",{}];",
		"let jobList = [" + row + "]; jobList = [];",
		"let jobList = [" + row + "]; jobList.splice(0);",
		`let jobList = [{type:"Track",title:compute(),desc:"Body",link:"https://example.test/42"}];`,
		`let jobList = [{type:"Track",title:"A",title:"B",desc:"Body",link:"https://example.test/42"}];`,
	} {
		if _, err := ParseYumChinaJobs(source); err == nil {
			t.Fatal("invalid or mutated literal acquired absence authority")
		}
	}
	for _, source := range []string{"", `<script src="https://foreign.example.test/job.js"></script>`, `<script src="js/job.js"></script><script src="js/job.js"></script>`} {
		if _, err := YumChinaScriptURL(source); err == nil {
			t.Fatal("missing or ambiguous public asset accepted")
		}
	}
	if endpoint, err := YumChinaScriptURL(`<script src="js/job.js"></script>`); err != nil || endpoint != YumChinaJobsURL {
		t.Fatal("published relative asset not resolved", err)
	}
}
