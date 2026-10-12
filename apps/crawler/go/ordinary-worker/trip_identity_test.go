package worker

import (
	"context"
	"testing"
)

func TestTripPostingFragmentSurvivesCanonicalInventory(t *testing.T) {
	board := "https://careers.trip.com/#/jobList"
	source := "https://careers.trip.com/#/job-detail?fromId=aB12cD34&atsApiType=Moka_Overseas"
	got, err := NormalizeRichInventory(context.Background(), board, []RichMonitorJob{{URL: source}}, false)
	if err != nil || len(got.Jobs) != 1 || got.Jobs[0].URL != source || len(got.DropReasons) != 0 {
		t.Fatal("verified SPA identity lost", err)
	}
	for _, bad := range []string{
		"https://careers.trip.com/", board,
		"https://careers.trip.com/#/job-detail?fromId=aB12cD34",
		"https://careers.trip.com/#/job-detail?fromId=aB12cD34&atsApiType=unknown",
		"https://careers.trip.com/#/job-detail?fromId=aB12cD34&fromId=other&atsApiType=Moka_Overseas",
		"https://careers.trip.com/#/job-detail?fromId=aB12cD34&atsApiType=Moka_Overseas&extra=x",
		"https://careers.trip.com/#/job-detail?fromId=&atsApiType=Moka_Overseas",
		"https://careers.trip.com/#/job-detail?fromId=unsafe%2Froute&atsApiType=Moka_Overseas",
		"https://foreign.example/#/job-detail?fromId=aB12cD34&atsApiType=Moka_Overseas",
	} {
		if classifyJobURL(bad, board) == "" {
			t.Fatal("invalid root identity accepted", bad)
		}
	}
	if classifyJobURL(source, "https://foreign.example/") == "" {
		t.Fatal("foreign board acquired Trip identity authority")
	}
}
