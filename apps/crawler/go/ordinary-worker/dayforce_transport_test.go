package worker

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"testing"
	"time"

	df "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/dayforcesession"
	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
)

type dayforceTransportFixture struct {
	pages []*df.Page
	calls int
}

func (s *dayforceTransportFixture) Search(offset int) (*df.Page, error) {
	if s.calls >= len(s.pages) {
		return nil, errors.New("unexpected fixture contact")
	}
	p := s.pages[s.calls]
	s.calls++
	p.Offset = offset
	return p, nil
}
func (*dayforceTransportFixture) Finish(bool) error { return nil }
func (*dayforceTransportFixture) Close()            {}

func TestDayforceSearchTransportMatchesActualPythonRetryAndPolicy(t *testing.T) {
	body, e := os.ReadFile("testdata/python_dayforce_transport.json")
	if e != nil {
		t.Fatal(e)
	}
	var cases []struct {
		Name  string
		Pages []struct {
			Status  int
			Headers map[string]string
			Body    string
		}
		Calls   int
		Delays  []float64
		Outcome string
	}
	if json.Unmarshal(body, &cases) != nil {
		t.Fatal("invalid frozen Python transport reference")
	}
	for _, c := range cases {
		t.Run(c.Name, func(t *testing.T) {
			const source = "https://jobs.dayforcehcm.com/api/geo/fixture/jobposting/search"
			s := &dayforceTransportFixture{}
			for _, p := range c.Pages {
				signals := &runtimev1.ResourcePolicySignals{}
				if v, ok := p.Headers["tdm-reservation"]; ok {
					signals.TdmReservationHeader = &v
				}
				s.pages = append(s.pages, &df.Page{FinalURL: source, Status: p.Status, Body: []byte(p.Body), Policy: signals})
			}
			var waits []time.Duration
			_, response, err := fetchDayforceSessionPage(context.Background(), s, source, 0, func(_ context.Context, d time.Duration) error { waits = append(waits, d); return nil })
			outcome := "ok"
			if err != nil {
				outcome = "failed"
				var r *policy.Reservation
				if errors.As(err, &r) {
					outcome = "reserved"
					if response == nil || !response.reserved {
						t.Fatal("reservation evidence lost")
					}
				}
			}
			if s.calls != c.Calls || outcome != c.Outcome || len(waits) != len(c.Delays) {
				t.Fatalf("Python transport differs: calls=%d/%d outcome=%s/%s waits=%d/%d", s.calls, c.Calls, outcome, c.Outcome, len(waits), len(c.Delays))
			}
			for i, d := range waits {
				base := time.Second * time.Duration(1<<i)
				if d < base/2 || d >= base*3/2 {
					t.Fatal("Python exponential jitter bounds changed")
				}
			}
		})
	}
}
