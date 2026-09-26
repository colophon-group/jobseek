package bookingapi

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"regexp"
)

const postingPrefix = "https://jobs.booking.com/booking/jobs/"
const postingSuffix = "?lang=en-us"

var slugRE = regexp.MustCompile(`^[1-9][0-9]{0,11}$`)

type Inventory struct {
	URLs       []string `json:"urls"`
	Advertised int      `json:"advertised"`
}

// Parse emits the same page-one URL set as the current configured DOM
// monitor. The API's totalCount is retained for coverage reporting: the
// current DOM monitor does not paginate beyond page one either.
func Parse(body []byte) (Inventory, error) {
	decoder := json.NewDecoder(bytes.NewReader(body))
	decoder.UseNumber()
	var root map[string]any
	if err := decoder.Decode(&root); err != nil || root == nil {
		return Inventory{}, errors.New("Booking jobs response must be a JSON object")
	}
	var trailing any
	if err := decoder.Decode(&trailing); err != io.EOF {
		return Inventory{}, errors.New("Booking jobs response has trailing JSON")
	}
	items, ok := root["jobs"].([]any)
	if !ok {
		return Inventory{}, errors.New("Booking jobs must be a list")
	}
	if len(items) > 100 {
		return Inventory{}, errors.New("Booking page-one jobs exceeded bound")
	}
	count, ok := root["totalCount"].(json.Number)
	if !ok {
		return Inventory{}, errors.New("Booking totalCount must be numeric")
	}
	advertised, err := count.Int64()
	if err != nil || advertised < 0 || advertised > 1_000_000 || advertised < int64(len(items)) {
		return Inventory{}, errors.New("Booking totalCount is inconsistent")
	}
	if len(items) == 0 && advertised > 0 {
		return Inventory{}, errors.New("Booking returned an empty page for nonempty inventory")
	}
	result := Inventory{URLs: make([]string, 0, len(items)), Advertised: int(advertised)}
	seen := map[string]bool{}
	for index, raw := range items {
		item, ok := raw.(map[string]any)
		if !ok {
			return Inventory{}, fmt.Errorf("Booking job %d must be an object", index)
		}
		data, ok := item["data"].(map[string]any)
		if !ok {
			return Inventory{}, fmt.Errorf("Booking job %d data must be an object", index)
		}
		slug, ok := data["slug"].(string)
		if !ok || !slugRE.MatchString(slug) || seen[slug] {
			return Inventory{}, fmt.Errorf("Booking job %d has invalid or duplicate slug", index)
		}
		seen[slug] = true
		result.URLs = append(result.URLs, postingPrefix+slug+postingSuffix)
	}
	return result, nil
}
