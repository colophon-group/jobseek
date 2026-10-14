package apisniffer

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"reflect"
	"testing"
)

func TestSingleJobSelectionMatchesOriginalBrowserDetail(t *testing.T) {
	body, err := os.ReadFile("testdata/python_single_job.json")
	if err != nil {
		t.Fatal(err)
	}
	var cases []struct {
		Name, Path string
		Bodies     []json.RawMessage
		Expected   map[string]any
		Owner      int
	}
	if json.Unmarshal(body, &cases) != nil || len(cases) < 100 {
		t.Fatal("complete original selector corpus unavailable")
	}
	for _, c := range cases {
		t.Run(c.Name, func(t *testing.T) {
			documents := []*Document{}
			for _, body := range c.Bodies {
				document, err := Decode(body)
				if err != nil {
					t.Fatal(err)
				}
				documents = append(documents, document)
			}
			owner, selected, err := FindSingleJob(context.Background(), documents, c.Path)
			if err != nil {
				t.Fatal(err)
			}
			body, _ := json.Marshal(selected)
			var normalized map[string]any
			if json.Unmarshal(body, &normalized) != nil || !reflect.DeepEqual(normalized, c.Expected) {
				t.Fatal("original selected object differs")
			}
			index := -1
			for i, document := range documents {
				if document == owner {
					index = i
				}
			}
			if index != c.Owner {
				t.Fatal("original response order or provider document changed")
			}
		})
	}
}

func TestSingleJobSelectionCancellationAndInvalidPath(t *testing.T) {
	document, err := Decode([]byte(`{"title":"Role"}`))
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, _, err := FindSingleJob(ctx, []*Document{document}, ""); !errors.Is(err, context.Canceled) {
		t.Fatal("canceled selector continued")
	}
	if _, _, err := FindSingleJob(context.Background(), []*Document{document}, "invalid["); err == nil {
		t.Fatal("invalid original JSON path accepted")
	}
}

func TestSingleJobProjectionMatchesOriginalBrowserDetail(t *testing.T) {
	body, err := os.ReadFile("testdata/python_single_job_projection.json")
	if err != nil {
		t.Fatal(err)
	}
	var cases []struct {
		Name     string
		Object   json.RawMessage
		Fields   map[string]any
		Expected map[string]any
		Failed   bool
	}
	if json.Unmarshal(body, &cases) != nil || len(cases) < 200 {
		t.Fatal("complete original projection corpus unavailable")
	}
	for _, c := range cases {
		t.Run(c.Name, func(t *testing.T) {
			document, err := Decode(c.Object)
			if err != nil {
				t.Fatal(err)
			}
			values, err := ProjectSingleJobDetail(document, document.Value.(map[string]any), c.Fields)
			if c.Failed {
				if err == nil {
					t.Fatal("original invalid projection became successful")
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			for key, expected := range c.Expected {
				actual, _ := json.Marshal(values[key])
				wanted, _ := json.Marshal(expected)
				if string(actual) != string(wanted) {
					t.Fatal("original detail field differs", key)
				}
			}
		})
	}
}
