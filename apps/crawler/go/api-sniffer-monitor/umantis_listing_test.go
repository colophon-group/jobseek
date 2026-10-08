package apisniffer

import (
	"encoding/json"
	"os"
	"reflect"
	"testing"
)

func TestUmantisListingAndNavigationMatchActualPython(t *testing.T) {
	var corpus struct {
		Rows []struct {
			Name, Body    string
			Strict, Error bool
			Rows          []struct {
				ID, Language, URL, Title, Location string
				EmploymentType                     string `json:"employment_type"`
			}
		}
		Navigation []struct {
			Name, Body string
			Error      bool
			Value      *struct {
				Table                    string `json:"table_nr"`
				Total, First, Last, Page int
				Next                     *string `json:"next_url"`
				Active                   bool    `json:"next_active"`
			}
		}
		Paths []struct{ Listing, Expected string }
	}
	raw, err := os.ReadFile("testdata/python_umantis.json")
	if err != nil || json.Unmarshal(raw, &corpus) != nil || len(corpus.Rows) != 14 || len(corpus.Navigation) != 9 || len(corpus.Paths) != 3 {
		t.Fatal("actual Python corpus missing")
	}
	o := UmantisOptions{Origin: "https://recruitingapp-3040.umantis.com", Employer: "Université de Neuchâtel", EmployerField: "column_value_1184173"}
	for _, c := range corpus.Rows {
		t.Run(c.Name, func(t *testing.T) {
			o.Strict = c.Strict
			rows, err := ParseUmantisRows(c.Body, o)
			if err == nil {
				rows, err = DeduplicateUmantisRows(rows)
			}
			if (err != nil) != c.Error {
				t.Fatal(rows, err, c.Error)
			}
			if c.Error {
				return
			}
			expected := []UmantisRow{}
			for _, r := range c.Rows {
				expected = append(expected, UmantisRow{ID: r.ID, Language: r.Language, URL: r.URL, Title: r.Title, Location: r.Location, EmploymentType: r.EmploymentType})
			}
			if !reflect.DeepEqual(rows, expected) {
				t.Fatal(rows, expected)
			}
		})
	}
	for _, c := range corpus.Navigation {
		t.Run(c.Name, func(t *testing.T) {
			n, err := ParseUmantisNavigation(c.Body)
			if (err != nil) != c.Error {
				t.Fatal(n, err)
			}
			if c.Error {
				return
			}
			if n == nil || c.Value == nil {
				t.Fatal(n, c.Value)
			}
			next := ""
			if c.Value.Next != nil {
				next = *c.Value.Next
			}
			want := UmantisNavigation{Table: c.Value.Table, Total: c.Value.Total, First: c.Value.First, Last: c.Value.Last, Page: c.Value.Page, Next: next, NextActive: c.Value.Active}
			if *n != want {
				t.Fatal(n, want)
			}
		})
	}
	for _, c := range corpus.Paths {
		o.Listing = c.Listing
		actual, err := o.PaginationURL("11", 2)
		if err != nil || actual != c.Expected {
			t.Fatal(actual, c.Expected, err)
		}
	}
}

func TestUmantisStrictNextLinkCannotLoseTenantTokenOrAdvanceWrongPage(t *testing.T) {
	o := UmantisOptions{Origin: "https://recruitingapp-3040.umantis.com"}
	current := o.Origin + "/Jobs/3?CompanyID=32"
	n := UmantisNavigation{Table: "11", Page: 1, NextActive: true}
	for _, c := range []struct {
		raw   string
		valid bool
	}{
		{"/Jobs/3?CompanyID=32&tc11=p2&_search_token11=123#top", true},
		{"https://foreign.example/Jobs/3?tc11=p2&_search_token11=123", false},
		{"/Jobs/All?tc11=p2&_search_token11=123", false},
		{"/Jobs/3?tc11=p3&_search_token11=123", false},
		{"/Jobs/3?tc11=p2", false},
		{"/Jobs/3?tc11=p2&tc11=p2&_search_token11=123", false},
	} {
		n.Next = c.raw
		_, err := o.NextURL(n, current)
		if (err == nil) != c.valid {
			t.Fatal(c, err)
		}
	}
}
