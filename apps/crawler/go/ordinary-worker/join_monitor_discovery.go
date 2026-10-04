package worker

import (
	"context"
	"net/http"
	"net/http/cookiejar"

	join "github.com/colophon-group/jobseek/apps/crawler/go/join-monitor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func discoverJoinInventory(ctx context.Context, client *http.Client, profile queue.GreenhouseMonitorProfile) (RichDiscovery, error) {
	if client == nil || profile.Profile != "join.nextdata-urls/v1" {
		return RichDiscovery{}, queue.ErrConfiguration
	}
	operationClient := *client
	jar, err := cookiejar.New(nil)
	if err != nil {
		return RichDiscovery{}, err
	}
	operationClient.Jar = jar
	operationClient.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	found, err := join.Fetch(ctx, &operationClient, profile.Endpoint, profile.Token)
	result := RichDiscovery{Jobs: []RichMonitorJob{}}
	if found.Responses > 0 {
		result.Response = &GreenhouseResponse{endpoint: profile.Endpoint, finalURL: found.FinalURL, status: found.Status}
	}
	if found.ErrorKind == "tdm" {
		if !queue.JoinMonitorResourceMatches(profile, found.ReservationInitialURL) || found.ReservationURL == "" {
			return result, queue.ErrConfiguration
		}
		result.Response = &GreenhouseResponse{endpoint: found.ReservationInitialURL, finalURL: found.ReservationURL, reserved: true, reservationSource: found.TDMSource}
		if found.TDMPolicy != "" {
			result.Response.policy = &found.TDMPolicy
		}
	}
	if ctx.Err() != nil {
		return result, ctx.Err()
	}
	if err != nil {
		return result, &DiscoveryError{Kind: "inventory_failed", cause: err}
	}
	for _, raw := range found.URLs {
		result.Jobs = append(result.Jobs, RichMonitorJob{URL: raw})
	}
	return result, nil
}
