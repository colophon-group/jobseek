package queue

import (
	"context"
	"errors"
	"testing"
)

func TestRealOwnedResourceRejectsOtherInitialTokenAndUnsafeResource(t *testing.T) {
	for _, kind := range []string{"gone", "reserved"} {
		t.Run(kind, func(t *testing.T) {
			f, a := lifecycleFixture(t, `{}`)
			ctx := context.Background()
			cycle := beginLifecycle(t, a)
			initial := "https://boards-api.greenhouse.io/v1/boards/fixture/jobs?content=true"
			for _, item := range []struct{ source, final string }{
				{"https://boards-api.greenhouse.io/v1/boards/other/jobs?content=true", "https://final.example.com/resource"},
				{initial, "https://user:password@final.example.com/resource"},
				{initial, "file:///tmp/resource"}, {initial, "/resource"}, {initial, "https://final.example.com/\x00"},
			} {
				var result *GreenhouseCycleResult
				var err error
				if kind == "gone" {
					result, err = cycle.FinishProviderGoneResource(ctx, item.source, GreenhouseGoneObservation{Endpoint: item.final, HTTPStatus: 404})
				} else {
					result, err = cycle.FinishReservationResource(ctx, item.source, &GreenhouseHeaderReservation{Endpoint: item.final})
				}
				if !errors.Is(err, ErrConfiguration) || result != nil {
					t.Fatal("unbound/unsafe resource acquired receipt")
				}
			}
			var reserved bool
			var gone, failures int
			if err := f.observer.QueryRow(ctx, "SELECT tdm_reserved,gone_confirmation_count,consecutive_failures FROM job_board WHERE id=$1::uuid", f.task.ID).Scan(&reserved, &gone, &failures); err != nil || reserved || gone != 0 || failures != 0 {
				t.Fatal("rejected resource changed lifecycle")
			}
		})
	}
}
