package bookingapi

import "testing"

func TestParsePageOneURLs(t *testing.T) {
	result, err := Parse([]byte(`{"jobs":[{"data":{"slug":"30336"}},{"data":{"slug":"13402"}}],"count":92,"totalCount":92}`))
	if err != nil || result.Advertised != 92 || len(result.URLs) != 2 ||
		result.URLs[0] != "https://jobs.booking.com/booking/jobs/30336?lang=en-us" ||
		result.URLs[1] != "https://jobs.booking.com/booking/jobs/13402?lang=en-us" {
		t.Fatalf("unexpected inventory: %#v, %v", result, err)
	}
}

func TestParseRejectsPartialInventory(t *testing.T) {
	for _, body := range []string{
		`{"jobs":{},"totalCount":1}`,
		`{"jobs":[],"totalCount":1}`,
		`{"jobs":[{"data":{"slug":"not-a-number"}}],"totalCount":1}`,
		`{"jobs":[{"data":{"slug":"1"}},{"data":{"slug":"1"}}],"totalCount":2}`,
		`{"jobs":[],"totalCount":0}{"jobs":[]}`,
	} {
		if _, err := Parse([]byte(body)); err == nil {
			t.Fatalf("accepted malformed response %s", body)
		}
	}
}
