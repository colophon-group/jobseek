package worker

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	"io"
	"net"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"sync"
)

func runtimeClaimUsesProxy(ctx context.Context, authority *queue.Authority, claim *queue.Claim) (bool, error) {
	if authority == nil || claim == nil || !claim.OwnershipBound() {
		return false, queue.ErrConfiguration
	}
	if claim.Descriptor().Kind == queue.Monitor {
		// Select from the same compiled ownership profile that execution checks.
		// A second provider-name allowlist can drift as new proxy profiles ship.
		task := claim.Descriptor()
		profile, err := queue.InspectRichMonitor(task.ID, task.Config)
		if err != nil {
			return false, err
		}
		return queue.ProfileRequiresProxy(profile.Profile), nil
	}
	if claim.Descriptor().Kind != queue.Scrape {
		return false, queue.ErrConfiguration
	}
	if claim.RecoveredReceipt() != nil {
		return false, nil // Settlement recovery performs no HTTP request.
	}
	// Scrape snapshots contain posting fields, not the canonical board metadata.
	// Resolve the already-bound detail profile before choosing a sealed client;
	// RunDetail independently revalidates it before fetching or writing.
	detail, err := authority.ReadWorkdayDetail(ctx, claim)
	if err != nil {
		return false, err
	}
	return queue.ProfileRequiresProxy(detail.Profile().Profile), nil
}

// All endpoint authority comes from protected startup settings. Configs and
// errors deliberately have no printable credentials or provider response text.
type proxyRuntimeConfig struct {
	endpoints   []*url.URL
	forced      int
	enabled     bool
	poolEntries int
}

func (proxyRuntimeConfig) String() string   { return "native proxy configuration" }
func (proxyRuntimeConfig) GoString() string { return "native proxy configuration" }

func readProxyRuntimeConfig(getenv func(string) string) (proxyRuntimeConfig, error) {
	c := proxyRuntimeConfig{forced: -1}
	provider := strings.ToLower(strings.TrimSpace(getenv("PROXY_PROVIDER")))
	if provider == "" {
		provider = "none"
	}
	if provider != "none" && provider != "webshare" {
		return c, ErrStartup
	}
	c.enabled = provider == "webshare"
	var raws []string
	if raw := getenv("WEBSHARE_PROXY_URLS"); raw != "" {
		if len(raw) > 1<<20 || json.Unmarshal([]byte(raw), &raws) != nil || len(raws) > 64 {
			return c, ErrStartup
		}
	}
	backbone := len(raws) > 0
	c.poolEntries = len(raws)
	if !backbone && getenv("WEBSHARE_PROXY_URL") != "" {
		raws = []string{getenv("WEBSHARE_PROXY_URL")}
	}
	seen := map[string]bool{}
	for _, raw := range raws {
		raw = strings.TrimSpace(raw)
		u, err := url.Parse(raw)
		if err != nil || u.Opaque != "" || u.Hostname() == "" || u.Scheme != "http" && u.Scheme != "https" && u.Scheme != "socks5" || u.Path != "" && u.Path != "/" || u.RawQuery != "" || u.Fragment != "" || u.ForceQuery {
			return c, ErrStartup
		}
		if port := u.Port(); port != "" {
			n, e := strconv.Atoi(port)
			if e != nil || n < 1 || n > 65535 {
				return c, ErrStartup
			}
		}
		if backbone {
			if u.Hostname() != "p.webshare.io" || u.Port() == "" || u.User == nil || u.User.Username() == "" {
				return c, ErrStartup
			}
			if _, ok := u.User.Password(); !ok {
				return c, ErrStartup
			}
		}
		raw = strings.TrimSuffix(raw, "/")
		u.Path = ""
		if seen[raw] {
			return c, ErrStartup
		}
		seen[raw] = true
		c.endpoints = append(c.endpoints, u)
	}
	if raw := getenv("WEBSHARE_PROXY_CANARY_SLOT"); raw != "" {
		n, err := strconv.Atoi(raw)
		if err != nil || n < 0 || n >= len(c.endpoints) {
			return c, ErrStartup
		}
		c.forced = n
	}
	return c, nil
}

// ProxyPreflight validates only protected proxy settings, without opening
// network clients or requiring database, queue, or ownership configuration.
func ProxyPreflight(getenv func(string) string) (string, error) {
	c, err := readProxyRuntimeConfig(getenv)
	if err != nil || c.enabled && len(c.endpoints) == 0 {
		return "", ErrStartup
	}
	mode := "disabled"
	if c.enabled {
		mode = "legacy_direct"
		if c.poolEntries > 0 {
			mode = "backbone_pool"
		}
	}
	return fmt.Sprintf("Runtime proxy configuration valid: mode=%s, pool_entries=%d", mode, c.poolEntries), nil
}

type proxyEndpointError struct{ reason string }

func (e *proxyEndpointError) Error() string { return "native proxy endpoint failure" }

type proxyLeaseContextKey struct{}

type rotatingProxyTransport struct {
	pool       *proxyPool
	base       *directTransport
	endpoints  []*url.URL
	mu         sync.Mutex
	transports []*directTransport
}

func newVerifiedProxyHTTP(config DirectHTTPConfig, proxy proxyRuntimeConfig) (*VerifiedHTTP, error) {
	client, err := NewDirectHTTP(config)
	if err != nil {
		return nil, err
	}
	if !proxy.enabled {
		return &VerifiedHTTP{client: client, proxyRequired: true}, nil
	}
	pool, err := newLiveProxyPool(len(proxy.endpoints), proxy.forced)
	if err != nil {
		return nil, err
	}
	t := &rotatingProxyTransport{pool: pool, base: client.Transport.(*directTransport), endpoints: proxy.endpoints, transports: make([]*directTransport, len(proxy.endpoints))}
	client.Transport = t
	client.CheckRedirect = func(request *http.Request, via []*http.Request) error {
		if len(via) > 20 {
			if request.Response != nil {
				markProxyRedirect(request.Response, true)
			}
			return errors.New("native proxy redirect limit")
		}
		// net/http calls CheckRedirect before draining/closing the previous
		// response. Its intermediate body cannot declare endpoint recovery.
		if request.Response != nil {
			markProxyRedirect(request.Response, false)
		}
		return nil
	}
	return &VerifiedHTTP{client: client, proxyRequired: true}, nil
}

func (t *rotatingProxyTransport) endpoint(slot int) *directTransport {
	t.mu.Lock()
	defer t.mu.Unlock()
	if existing := t.transports[slot]; existing != nil {
		return existing
	}
	base := t.base
	endpoint := &directTransport{allowed: base.allowed, lookup: base.lookup, dial: base.dial, timeout: base.timeout, requests: base.requests, connections: base.connections, proxy: t.endpoints[slot]}
	endpoint.evictIdle = t.CloseIdleConnections
	endpoint.inner = base.inner.Clone()
	endpoint.inner.Proxy = http.ProxyURL(endpoint.proxy)
	endpoint.inner.DialContext = endpoint.dialContext
	endpoint.inner.OnProxyConnectResponse = func(_ context.Context, _ *url.URL, _ *http.Request, response *http.Response) error {
		if response.StatusCode == 407 {
			return &proxyEndpointError{"proxy_auth"}
		}
		if response.StatusCode != 200 || response.Header.Get("X-Webshare-Error-Reason") != "" {
			return &proxyEndpointError{"proxy_transport"}
		}
		return nil
	}
	t.transports[slot] = endpoint
	return endpoint
}
func (t *rotatingProxyTransport) discard(slot int) {
	t.mu.Lock()
	old := t.transports[slot]
	t.transports[slot] = nil
	t.mu.Unlock()
	if old != nil {
		old.CloseIdleConnections()
	}
}
func (t *rotatingProxyTransport) CloseIdleConnections() {
	t.mu.Lock()
	defer t.mu.Unlock()
	for _, endpoint := range t.transports {
		if endpoint != nil {
			endpoint.CloseIdleConnections()
		}
	}
}
func (t *rotatingProxyTransport) failure(selection *proxySelection, origin string, err error, ctx context.Context) {
	var endpoint *proxyEndpointError
	var network net.Error
	if errors.As(err, &endpoint) {
		t.pool.failure(selection, origin, endpoint.reason)
		t.discard(selection.slot)
	} else if ctx.Err() != nil {
		t.pool.abandon(selection)
	} else if errors.As(err, &network) || errors.Is(err, io.ErrUnexpectedEOF) {
		reason := "origin_transport"
		var operation *net.OpError
		if errors.As(err, &operation) && operation.Op == "socks connect" {
			reason = "proxy_transport"
		}
		t.pool.failure(selection, origin, reason)
		if reason == "proxy_transport" {
			t.discard(selection.slot)
		}
	} else {
		t.pool.abandon(selection)
	}
}
func (t *rotatingProxyTransport) RoundTrip(original *http.Request) (*http.Response, error) {
	if original.URL == nil {
		return nil, ErrUnsafeURL
	}
	origin, err := directHost(original.URL.Hostname())
	if err != nil {
		return nil, ErrUnsafeURL
	}
	selection, _ := original.Context().Value(proxyLeaseContextKey{}).(*proxySelection)
	if original.Response != nil && original.Response.Request != nil {
		selection, _ = original.Response.Request.Context().Value(proxyLeaseContextKey{}).(*proxySelection)
	}
	if selection == nil || selection.pool != t.pool {
		selection, err = t.pool.selectEndpoint(origin)
		if err != nil {
			return nil, err
		}
	}
	request := original.Clone(context.WithValue(original.Context(), proxyLeaseContextKey{}, selection))
	response, err := t.endpoint(selection.slot).RoundTrip(request)
	if err != nil {
		t.failure(selection, origin, err, request.Context())
		return nil, err
	}
	response.Request = request
	outcome := &proxyOutcomeBody{ReadCloser: response.Body, owner: t, selection: selection, origin: origin, status: response.StatusCode, ctx: request.Context()}
	response.Body = outcome
	if response.StatusCode == 407 {
		outcome.finishFailure(&proxyEndpointError{"proxy_auth"})
	} else if response.Header.Get("X-Webshare-Error-Reason") != "" {
		outcome.finishFailure(&proxyEndpointError{"proxy_transport"})
	}
	return response, nil
}

type proxyOutcomeBody struct {
	io.ReadCloser
	owner     *rotatingProxyTransport
	selection *proxySelection
	origin    string
	status    int
	ctx       context.Context
	once      sync.Once
	redirect  bool
}

func markProxyRedirect(response *http.Response, abandon bool) {
	if body, ok := response.Body.(*proxyOutcomeBody); ok {
		body.redirect = true
		if abandon {
			body.once.Do(func() { body.owner.pool.abandon(body.selection) })
		}
	}
}
func (b *proxyOutcomeBody) finishFailure(err error) {
	b.once.Do(func() { b.owner.failure(b.selection, b.origin, err, b.ctx) })
}
func (b *proxyOutcomeBody) finishSuccess() {
	if b.redirect {
		return
	}
	b.once.Do(func() {
		if b.status == 401 || b.status == 403 || b.status == 429 {
			b.owner.pool.failure(b.selection, b.origin, "origin_block")
		} else {
			b.owner.pool.success(b.selection)
		}
	})
}
func (b *proxyOutcomeBody) Read(p []byte) (int, error) {
	n, err := b.ReadCloser.Read(p)
	if err == io.EOF {
		b.finishSuccess()
	} else if err != nil {
		b.finishFailure(err)
	}
	return n, err
}
func (b *proxyOutcomeBody) Close() error {
	err := b.ReadCloser.Close()
	if !b.redirect {
		// An early policy/body-limit return is inconclusive for a recovery
		// probe. Concrete origin denial still scopes quarantine to that host.
		if b.status == 401 || b.status == 403 || b.status == 429 {
			b.finishSuccess()
		} else {
			b.once.Do(func() { b.owner.pool.abandon(b.selection) })
		}
	}
	return err
}
