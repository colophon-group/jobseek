package main

import (
	"testing"
	"time"

	"github.com/colophon-group/jobseek/pilots/go-http-sitemap/boundedhttp"
)

func TestSettledConnectionStatsRetriesTransientCloseSnapshot(t *testing.T) {
	calls := 0
	stats := settledConnectionStats(func() boundedhttp.ConnectionStats {
		calls++
		if calls < 3 {
			return boundedhttp.ConnectionStats{Open: 9, InUsePermits: 10}
		}
		return boundedhttp.ConnectionStats{Open: 9, InUsePermits: 9}
	}, time.Second)

	if calls != 3 || stats.Open != 9 || stats.InUsePermits != 9 {
		t.Fatalf("settled stats = %+v after %d calls", stats, calls)
	}
}

func TestSettledConnectionStatsFailsClosed(t *testing.T) {
	defer func() {
		if recover() == nil {
			t.Fatal("expected persistent mismatch to panic")
		}
	}()
	settledConnectionStats(func() boundedhttp.ConnectionStats {
		return boundedhttp.ConnectionStats{Open: 9, InUsePermits: 10}
	}, 0)
}
