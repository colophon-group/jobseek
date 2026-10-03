package worker

import (
	"context"
	"errors"
	"net/http"
	"time"
)

// CheckWorkerHealth replaces the four legacy container probes without loading
// worker assets, ownership state or database credentials. These root endpoints
// retain their existing success contract while the workers migrate separately.
func CheckWorkerHealth(ctx context.Context, port string) error {
	switch port {
	case "9095", "9096", "9097", "9098":
		return checkWorkerHealthEndpoint(ctx, "http://127.0.0.1:"+port+"/")
	default:
		return ErrStartup
	}
}

func checkWorkerHealthEndpoint(ctx context.Context, endpoint string) error {
	ctx, cancel := context.WithTimeout(ctx, 3*time.Second)
	defer cancel()
	transport := &http.Transport{Proxy: nil}
	defer transport.CloseIdleConnections()
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint, nil)
	if err != nil {
		return ErrStartup
	}
	response, err := (&http.Client{Transport: transport, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}).Do(request)
	if err != nil {
		return errors.New("worker health unavailable")
	}
	defer response.Body.Close()
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return errors.New("worker health rejected")
	}
	return nil
}
