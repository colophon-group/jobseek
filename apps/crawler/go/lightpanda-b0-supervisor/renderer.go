package main

import (
	"context"
	"crypto/tls"
	"crypto/x509"
	"errors"
	"io"
	"strconv"

	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	b0client "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/lightpandaclient"
)

const (
	rendererALPN     = "jobseek-lightpanda-b0/1"
	runtimeContract  = "crawler.runtime/v1"
	resultFrameLimit = uint64(2 * 1024 * 1024)
)

type renderer struct{ client *b0client.Client }
type reservation struct{ held *b0client.Reservation }

func newRenderer(c config) (*renderer, error) {
	client, err := b0client.New(b0client.Config{RendererAddress: c.RendererAddress, RendererServerName: c.RendererServerName, CAPath: c.CAPath, ClientCertificatePath: c.ClientCertificatePath, ClientKeyPath: c.ClientKeyPath, CAPin: c.CAPin, ServerLeafPin: c.ServerLeafPin, ServerSPKIPin: c.ServerSPKIPin})
	if err != nil {
		return nil, err
	}
	return &renderer{client}, nil
}
func (r *renderer) reserve(ctx context.Context) (heldReservation, error) {
	held, err := r.client.Reserve(ctx)
	if err != nil {
		return nil, err
	}
	return &reservation{held}, nil
}
func (r *reservation) close() {
	if r != nil && r.held != nil {
		r.held.Close()
	}
}
func (r *reservation) execute(ctx context.Context, task queueTask) ([]byte, error) {
	if r == nil || r.held == nil {
		return nil, errors.New("renderer reservation is unavailable")
	}
	input, err := browserInput(task)
	if err != nil {
		return nil, err
	}
	return r.held.Execute(ctx, input)
}
func exactRendererConnection(state tls.ConnectionState, ca *x509.Certificate, leafPin, spkiPin string) bool {
	return b0client.ExactConnection(state, ca, leafPin, spkiPin)
}
func writeAll(w io.Writer, p []byte) error { return b0client.WriteAll(w, p) }

func browserInput(task queueTask) (*runtimev1.BrowserExecutionInput, error) {
	if task.RenderAttempt < 0 || task.RenderAttempt > 1 || (task.RenderAttempt != 0 && task.Envelope.ScraperType != "dom") {
		return nil, errors.New("invalid render retry ordinal")
	}
	originID := "lightpanda-b0:" + task.PayloadSHA256
	if task.RenderAttempt != 0 {
		originID += ":challenge-retry-" + strconv.Itoa(task.RenderAttempt)
	}
	retries := uint32(0)
	if task.Envelope.ScraperType == "dom" {
		retries = 1
	}
	return b0client.NavigationInput(b0client.Navigation{URL: task.Envelope.SourceURL, RoutingRevision: task.Envelope.RoutingRevision, OriginRequestID: originID, Wait: task.Envelope.Wait, WaitFallback: task.Envelope.WaitFallback, TimeoutMS: uint64(task.Envelope.TimeoutMS), TransportRetries: retries})
}
