package worker

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestWorkerHealthRootResponseAndUnavailable(t *testing.T) {
	for _, status := range []int{200, 204, 302, 503} {
		server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if r.Method != "GET" || r.URL.Path != "/" {
				t.Error("worker root health resource changed")
			}
			w.Header().Set("Location", "http://127.0.0.1:1/")
			w.WriteHeader(status)
		}))
		err := checkWorkerHealthEndpoint(context.Background(), server.URL+"/")
		server.Close()
		if (err == nil) != (status >= 200 && status < 300) {
			t.Fatal("worker status/redirect readiness changed", status, err)
		}
		if checkWorkerHealthEndpoint(context.Background(), server.URL+"/") == nil {
			t.Fatal("stopped worker passed health")
		}
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if CheckWorkerHealth(ctx, "9095") == nil {
		t.Fatal("cancelled probe passed")
	}
	for _, port := range []string{"", "09095", "9093", "9104", "9095/path", "9095?query=1"} {
		if CheckWorkerHealth(context.Background(), port) != ErrStartup {
			t.Fatal("probe accepted an unconfigured worker port")
		}
	}
}
