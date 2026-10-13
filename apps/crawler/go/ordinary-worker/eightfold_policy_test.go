package worker

import (
	"context"
	"fmt"
	"net/http"
	"testing"
)

func TestEightfoldPublisherReservationPrecedesErrorStatusAndFallback(t *testing.T) {
	for _, status := range []int{404, 429, 503} {
		for _, source := range []string{"https://caci.wd1.myworkdayjobs.com/external/job/Engineer_123", "https://fixture.eightfold.ai/careers/job/123"} {
			t.Run(fmt.Sprintf("%d/%s", status, source), func(t *testing.T) {
				calls := 0
				client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					calls++
					w.Header().Set("TDM-Reservation", "1")
					w.WriteHeader(status)
				}))
				content, reserved, err := fetchEightfoldDetail(context.Background(), client.client, source, nil)
				if err != nil || content != nil || reserved == nil || calls != 1 {
					t.Fatal("reservation emitted content or API fallback", err, calls)
				}
			})
		}
	}
}
