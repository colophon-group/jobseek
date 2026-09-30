package executor

import (
	"bytes"
	"encoding/json"
	"os"
	"reflect"
	"testing"
)

func TestContentScalarsAndArraysMatchPython(t *testing.T) {
	data, err := os.ReadFile("testdata/python_content.json")
	if err != nil {
		t.Fatal(err)
	}
	var fixture struct {
		Coercion []struct {
			Input     any
			Text      *string
			Locations []string
		}
		Titles []struct {
			Input  *string
			Output []string
		}
		Locales []struct {
			Language         *string
			Detected, Output []string
		}
	}
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.UseNumber()
	if err := decoder.Decode(&fixture); err != nil {
		t.Fatal(err)
	}
	for _, c := range fixture.Coercion {
		text, err := CoerceText(c.Input)
		if err != nil || !reflect.DeepEqual(text, c.Text) {
			t.Fatalf("Python scalar mismatch for %+v: %v %v", c.Input, text, err)
		}
		locations, err := CoerceLocations(c.Input)
		if err != nil || !reflect.DeepEqual(locations, c.Locations) {
			t.Fatalf("Python location coercion mismatch for %+v: %v %v", c.Input, locations, err)
		}
	}
	for _, c := range fixture.Titles {
		titles := BuildTitles(c.Input)
		if len(titles) != len(c.Output) || len(titles) > 0 && !reflect.DeepEqual(titles, c.Output) {
			t.Fatalf("Python title mismatch: %q != %q", titles, c.Output)
		}
	}
	for _, c := range fixture.Locales {
		if actual := BuildLocales(c.Language, c.Detected); !reflect.DeepEqual(actual, c.Output) {
			t.Fatalf("Python locales mismatch: %q != %q", actual, c.Output)
		}
	}
}

func TestFrozenTitleAndEmploymentRules(t *testing.T) {
	for _, title := range contentRules.GarbageTitles {
		if !GarbageTitle(" \t" + title + "\x1c") {
			t.Fatalf("garbage title not recognized: %q", title)
		}
	}
	if GarbageTitle("Engineer") {
		t.Fatal("legitimate title rejected")
	}
	for raw, expected := range contentRules.EmploymentTypes {
		value := EmploymentType(&raw)
		if value == nil || *value != expected {
			t.Fatalf("employment type mismatch: %q", raw)
		}
	}
	for _, raw := range append(contentRules.InternSignals, "unknown") {
		if EmploymentType(&raw) != nil {
			t.Fatalf("employment signal incorrectly mapped: %q", raw)
		}
	}
}
