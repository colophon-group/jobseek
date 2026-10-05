package worker

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"net"
	"net/url"
	"os"
	"strconv"
	"strings"
	"time"

	b0task "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/b0task"
	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	lp "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/lightpandaclient"
	executor "github.com/colophon-group/jobseek/apps/crawler/go/lightpanda-b0-executor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
)

type renderedDetailClient interface {
	Fetch(context.Context, queue.WorkdayDetailProfile) (map[string]any, *policy.Reservation, error)
}

type NativeRenderedDetails struct {
	client *lp.Client
	slots  chan struct{}
}

// The installed service receives the same scoped claimant credential files as
// the existing B0 consumer. Board configuration cannot select these paths/host.
func installedRenderedDetails() (*NativeRenderedDetails, error) {
	const host = "10.0.0.5"
	readPin := func(name string) (string, error) {
		body, err := os.ReadFile("/run/lightpanda/" + name + ".sha256")
		if err != nil || len(body) != 65 || body[64] != '\n' || !planPattern.MatchString(string(body[:64])) {
			return "", ErrStartup
		}
		return strings.TrimSuffix(string(body), "\n"), nil
	}
	ca, err := readPin("ca")
	if err != nil {
		return nil, err
	}
	leaf, err := readPin("server-leaf")
	if err != nil {
		return nil, err
	}
	spki, err := readPin("server-spki")
	if err != nil {
		return nil, err
	}
	return NewNativeRenderedDetails(lp.Config{RendererAddress: net.JoinHostPort(host, "9443"), RendererServerName: host, CAPath: "/run/lightpanda/ca.pem", ClientCertificatePath: "/run/lightpanda/client.pem", ClientKeyPath: "/run/lightpanda/client-key.pem", CAPin: ca, ServerLeafPin: leaf, ServerSPKIPin: spki})
}

func NewNativeRenderedDetails(c lp.Config) (*NativeRenderedDetails, error) {
	client, err := lp.New(c)
	if err != nil {
		return nil, err
	}
	return &NativeRenderedDetails{client: client, slots: make(chan struct{}, 4)}, nil
}

func (r *NativeRenderedDetails) Fetch(ctx context.Context, profile queue.WorkdayDetailProfile) (map[string]any, *policy.Reservation, error) {
	if r == nil || r.client == nil {
		return nil, nil, queue.ErrConfiguration
	}
	select {
	case r.slots <- struct{}{}:
		defer func() { <-r.slots }()
	case <-ctx.Done():
		return nil, nil, ctx.Err()
	}
	options, scraper := profile.DOMConfig, "dom"
	if profile.Profile == "jsonld.rendered-detail/v1" {
		options, scraper = profile.JSONLDConfig, "json-ld"
	} else if profile.Profile != "dom.rendered-detail/v1" {
		return nil, nil, queue.ErrUnsupportedProfile
	}
	parser, err := json.Marshal(options)
	if err != nil {
		return nil, nil, queue.ErrConfiguration
	}
	wait, fallback, timeout := "networkidle", "domcontentloaded", uint64(30000)
	if value, ok := options["wait"].(string); ok {
		wait = value
	}
	var fallbackWait *string = &fallback
	if value, present := options["wait_fallback"]; present {
		if value == nil {
			fallbackWait = nil
		} else if text, ok := value.(string); ok {
			fallback = text
		}
	}
	if value, ok := options["timeout"].(float64); ok {
		timeout = uint64(value)
	}
	if value, ok := options["timeout"].(json.Number); ok {
		n, e := value.Int64()
		if e != nil || n < 1 {
			return nil, nil, queue.ErrConfiguration
		}
		timeout = uint64(n)
	}
	revision := profile.EffectiveBoardSHA256
	if value, ok := options["routing_revision"].(string); ok {
		revision = value
	}
	identity, err := b0task.CanonicalJSON(map[string]any{"board_id": profile.BoardID, "config": profile.EffectiveBoardSHA256, "source_url": profile.SourceURL}, true)
	if err != nil {
		return nil, nil, queue.ErrConfiguration
	}
	// Identity is attribution only; queue and write authority remain the held claim.
	digest := sha256.Sum256(identity)
	origin := "ordinary-detail:" + hex.EncodeToString(digest[:])
	for attempt := 0; attempt <= 1; attempt++ {
		requestID := origin
		if attempt > 0 {
			requestID += ":challenge-retry-" + strconv.Itoa(attempt)
		}
		retries := uint32(0)
		if scraper == "dom" {
			retries = 1
		}
		input, err := lp.NavigationInput(lp.Navigation{URL: profile.SourceURL, RoutingRevision: revision, OriginRequestID: requestID, Wait: wait, WaitFallback: fallbackWait, TimeoutMS: timeout, TransportRetries: retries})
		if err != nil {
			return nil, nil, err
		}
		held, err := waitRenderedReservation(ctx, r.client.Reserve)
		if err != nil {
			return nil, nil, err
		}
		body, err := held.Execute(ctx, input)
		held.Close()
		if err != nil {
			return nil, nil, err
		}
		result, err := executor.DecodeResult(body)
		if err != nil {
			return nil, nil, err
		}
		content, reservation, err := parseHeldRenderedResult(ctx, profile.SourceURL, scraper, parser, result)
		if reservation != nil {
			return nil, reservation, err
		}
		if scraper == "dom" && attempt == 0 && errors.Is(err, executor.ErrBotChallenge) {
			continue
		}
		if scraper == "json-ld" && attempt == 0 && err == nil && (content["title"] == nil || content["title"] == "") {
			timer := time.NewTimer(time.Second)
			select {
			case <-ctx.Done():
				timer.Stop()
				return nil, nil, ctx.Err()
			case <-timer.C:
			}
			continue
		}
		return content, nil, err
	}
	return nil, nil, executor.ErrBotChallenge
}

// Both native consumers share C4 capacity. A full renderer closes a new TLS
// connection; wait within the existing claim context instead of recording an
// upstream failure. The caller's heartbeat and cancellation remain in charge.
func waitRenderedReservation(ctx context.Context, reserve func(context.Context) (*lp.Reservation, error)) (*lp.Reservation, error) {
	for {
		held, err := reserve(ctx)
		if !errors.Is(err, lp.ErrReservationUnavailable) {
			return held, err
		}
		timer := time.NewTimer(time.Second)
		select {
		case <-ctx.Done():
			timer.Stop()
			return nil, ctx.Err()
		case <-timer.C:
		}
	}
}

func parseHeldRenderedResult(ctx context.Context, source, scraper string, parser json.RawMessage, result *runtimev1.BrowserResult) (map[string]any, *policy.Reservation, error) {
	content, err := executor.ParseRenderedDetail(source, scraper, parser, result)
	// Attribute only a held, validated navigation response. A renderer
	// connection/protocol failure is not evidence of an origin failure.
	if success := result.GetSuccess(); success != nil && !errors.Is(err, executor.ErrRenderedResult) && !errors.Is(err, executor.ErrProtocol) && !errors.Is(err, policy.ErrSignals) {
		requested, _ := url.Parse(source)
		final, _ := url.Parse(success.FinalUrl)
		observation := httpObservation(ctx)
		observation.noteRequest(requested.Hostname())
		observation.noteResponse(final.Hostname(), int(success.GetStatus()))
		observation.noteBytes(int(success.Html.TotalSizeBytes))
	}
	var reservation *policy.Reservation
	if errors.As(err, &reservation) {
		reservation.URL = result.GetSuccess().FinalUrl
		return nil, reservation, nil
	}
	return content, nil, err
}
