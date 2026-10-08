package apisniffer

import (
	"encoding/json"
	"os"
	"reflect"
	"testing"
	"time"
)

func TestUnifrDepartmentInventoriesAndDeadlinesMatchActualPython(t *testing.T) {
	var cases []struct {
		Kind, Name, Body, Text, Today string
		Options                       UnifrOptions
		Output                        json.RawMessage
		Error                         bool
	}
	raw, err := os.ReadFile("testdata/python_unifr_department.json")
	if err != nil || json.Unmarshal(raw, &cases) != nil || len(cases) != 29 {
		t.Fatal("actual Python department reference missing", err)
	}
	for _, c := range cases {
		t.Run(c.Kind+"/"+c.Name, func(t *testing.T) {
			var output any
			var err error
			switch c.Kind {
			case "deadline":
				output, err = UnifrDeadline(c.Text)
			case "links":
				output, err = UnifrLinkInventory([]byte(c.Body), c.Options)
			case "accordion":
				items, failure := UnifrAccordionItems([]byte(c.Body), c.Options)
				err = failure
				if err == nil {
					today, failure := time.Parse("2006-01-02", c.Today)
					if failure != nil {
						t.Fatal(failure)
					}
					output, err = UnifrAccordionJobs(items, c.Options, nil, today)
				}
			default:
				t.Fatal("unknown reference")
			}
			if (err != nil) != c.Error {
				t.Fatal(err, c.Error)
			}
			if c.Error {
				return
			}
			body, _ := json.Marshal(output)
			var actual, expected any
			if json.Unmarshal(body, &actual) != nil || json.Unmarshal(c.Output, &expected) != nil {
				t.Fatal("invalid field reference")
			}
			if c.Kind == "accordion" {
				keys := []string{"url", "title", "description", "locations", "date_posted", "language", "localizations", "extras", "metadata"}
				left, right := actual.([]any), expected.([]any)
				if len(left) != len(right) {
					t.Fatal(left, right)
				}
				for i, a := range left {
					a, b := a.(map[string]any), right[i].(map[string]any)
					for _, key := range keys {
						value := a[key]
						if value == "" {
							value = nil
						}
						if !reflect.DeepEqual(value, b[key]) {
							t.Fatal(key, value, b[key])
						}
					}
				}
			} else if !reflect.DeepEqual(actual, expected) {
				t.Fatal(actual, expected)
			}
		})
	}
}
