package apisniffer

import (
	"context"
	"encoding/json"
	"errors"
	"net/url"
	"os"
	"reflect"
	"strconv"
	"strings"
	"testing"
)

func TestTaleoActualPythonIdentityPagesAndDiscovery(t *testing.T) {
	var corpus struct {
		Board      TaleoBoard
		Identities []struct {
			URL           string
			Valid, Detail bool
			Board         *TaleoBoard
			RID, Offset   *int
		}
		Redirects []struct {
			Resource, Target, URL string
			Valid                 bool
			Board                 *TaleoBoard
		}
		Inactive []struct {
			Resource, Target string
			Gone             bool
		}
		Pages []struct {
			Name, Body  string
			Offset      int
			Total, Next *int
			URLs        []string
			Failed      bool
		}
		Inventories []struct {
			Name        string
			BoardURL    string `json:"board_url"`
			Metadata    map[string]any
			Bodies      map[string]*string
			Calls, URLs []string
			Failed      bool
		}
	}
	raw, err := os.ReadFile("testdata/python_taleo.json")
	if err != nil || json.Unmarshal(raw, &corpus) != nil || len(corpus.Identities) != 16 || len(corpus.Redirects) != 8 || len(corpus.Inactive) != 5 || len(corpus.Pages) != 20 || len(corpus.Inventories) != 12 {
		t.Fatal("actual Python Taleo corpus absent", err)
	}
	for i, c := range corpus.Identities {
		t.Run("identity/"+strconv.Itoa(i), func(t *testing.T) {
			b, detail, rid, offset, err := ParseTaleoURL(c.URL)
			if (err == nil) != c.Valid || c.Valid && (b != *c.Board || detail != c.Detail || !reflect.DeepEqual(offset, c.Offset) || c.RID != nil && rid != int64(*c.RID)) {
				t.Fatal(b, detail, rid, offset, err, c)
			}
		})
	}
	for i, c := range corpus.Redirects {
		t.Run("redirect/"+strconv.Itoa(i), func(t *testing.T) {
			u, b, err := TaleoSafeRedirect(corpus.Board, c.Resource, c.Target)
			if (err == nil) != c.Valid || c.Valid && (u != c.URL || b != *c.Board) {
				t.Fatal(u, b, err, c)
			}
		})
	}
	for i, c := range corpus.Inactive {
		t.Run("inactive/"+strconv.Itoa(i), func(t *testing.T) {
			if got := TaleoInactiveRedirect(corpus.Board, c.Resource, c.Target); got != c.Gone {
				t.Fatal(got, c)
			}
		})
	}
	for _, c := range corpus.Pages {
		t.Run("page/"+c.Name, func(t *testing.T) {
			p, err := ParseTaleoPage([]byte(c.Body), corpus.Board, c.Offset)
			if (err != nil) != c.Failed || err == nil && (!reflect.DeepEqual(p.URLs, c.URLs) || !reflect.DeepEqual(p.Total, c.Total) || !reflect.DeepEqual(p.Next, c.Next)) {
				t.Fatal(p, err, c)
			}
		})
	}
	for _, c := range corpus.Inventories {
		t.Run("inventory/"+c.Name, func(t *testing.T) {
			md, _ := json.Marshal(c.Metadata)
			o, err := TaleoOptionsFromMetadata(c.BoardURL, string(md))
			if err != nil {
				t.Fatal(err)
			}
			calls := []string{}
			out, err := DiscoverTaleo(context.Background(), o, func(ctx context.Context, resource string) ([]byte, error) {
				calls = append(calls, resource)
				u, e := url.Parse(resource)
				if e != nil {
					t.Fatal(e)
				}
				offset := u.Query().Get("rowFrom")
				if offset == "" {
					offset = "0"
				}
				body, ok := c.Bodies[offset]
				if !ok || body == nil {
					return nil, errors.New("later fixture request failed")
				}
				return []byte(*body), nil
			})
			if (err != nil) != c.Failed || err == nil && !reflect.DeepEqual(out, c.URLs) || !reflect.DeepEqual(calls, c.Calls) || err != nil && len(out) > 0 {
				t.Fatal(out, err, c.URLs, calls, c.Calls)
			}
		})
	}
}

func TestTaleoNumericOverflowCannotProveEmpty(t *testing.T) {
	for _, total := range []string{strings.Repeat("9", 100), "²"} {
		body := `<span class="oracletaleocwsv2-panel-number">` + total + `</span>`
		if _, err := ParseTaleoPage([]byte(body), TaleoBoard{"phe.tbe.taleo.net", "phe01", "ACME", 1}, 0); err == nil {
			t.Fatal("unrepresentable numeric total proved empty")
		}
	}
}
