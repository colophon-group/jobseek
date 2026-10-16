package worker

import (
	"context"
	"errors"
	"net/url"
	"sync"

	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/browserproxy"
	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	executor "github.com/colophon-group/jobseek/apps/crawler/go/lightpanda-b0-executor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

// Browser and HTTP operations share the same process-local endpoint health.
// A lease crosses the renderer boundary only as a credential-bound opaque ref.
type browserProxyLease struct {
	selection    *proxySelection
	once         sync.Once
	requestedURL string
}

func bindBrowserProxy(input *runtimev1.BrowserExecutionInput, options map[string]any, verified *VerifiedHTTP) (*browserProxyLease, error) {
	if options["proxy"] == nil || options["proxy"] == false {
		return nil, nil
	}
	if options["proxy"] != true || input == nil || input.Plan == nil || input.Plan.Session != nil || verified == nil || !verified.proxyRequired || verified.client == nil {
		return nil, queue.ErrConfiguration
	}
	transport, rotating := verified.client.Transport.(*rotatingProxyTransport)
	if !rotating {
		// readProxyRuntimeConfig's explicit provider=none retains the original
		// configured-proxy flag's direct behavior. No arbitrary client qualifies.
		if _, direct := verified.client.Transport.(*directTransport); direct {
			return nil, nil
		}
		return nil, queue.ErrConfiguration
	}
	u, err := url.Parse(input.Plan.TargetUrl)
	if err != nil || u.User != nil || (u.Scheme != "http" && u.Scheme != "https") {
		return nil, queue.ErrConfiguration
	}
	origin, err := directHost(u.Hostname())
	if err != nil {
		return nil, queue.ErrConfiguration
	}
	selection, err := transport.pool.selectEndpoint(origin)
	if err != nil {
		return nil, err
	}
	lease := &browserProxyLease{selection: selection, requestedURL: input.Plan.TargetUrl}
	endpoint, err := browserproxy.Parse(transport.endpoints[selection.slot].String())
	if err != nil {
		lease.abandon()
		return nil, queue.ErrConfiguration
	}
	ref := endpoint.Reference()
	input.Plan.Session = &runtimev1.SessionPlan{ProxyPolicyRef: &ref}
	input.Plan.RequiredCapabilities = append(input.Plan.RequiredCapabilities, runtimev1.BrowserCapability_BROWSER_CAPABILITY_PROXY)
	return lease, nil
}

func (lease *browserProxyLease) abandon() {
	if lease != nil {
		lease.once.Do(func() { lease.selection.pool.abandon(lease.selection) })
	}
}

func (lease *browserProxyLease) observe(ctx context.Context, result *runtimev1.BrowserResult, runErr error) {
	if lease == nil {
		return
	}
	lease.once.Do(func() {
		selection, pool := lease.selection, lease.selection.pool
		if ctx.Err() != nil || runErr != nil || result == nil {
			pool.abandon(selection)
			return
		}
		if failure := result.GetError().GetError(); failure != nil {
			if failure.Code == runtimev1.ErrorCode_ERROR_CODE_TRANSPORT || failure.Code == runtimev1.ErrorCode_ERROR_CODE_TIMEOUT {
				pool.failure(selection, selection.origin, "origin_transport")
			} else {
				pool.abandon(selection)
			}
			return
		}
		_, err := executor.RenderedHTML(result, lease.requestedURL)
		var status *executor.NavigationHTTPError
		if err != nil && !errors.As(err, &status) {
			pool.abandon(selection)
			return
		}
		if status != nil {
			u, err := url.Parse(status.ResponseURL)
			if err != nil {
				pool.abandon(selection)
				return
			}
			origin, err := directHost(u.Hostname())
			if err != nil {
				pool.abandon(selection)
				return
			}
			if status.Status == 407 {
				pool.failure(selection, origin, "proxy_auth")
				return
			}
			if status.Status == 401 || status.Status == 403 || status.Status == 429 {
				pool.failure(selection, origin, "origin_block")
				return
			}
		}
		pool.success(selection)
	})
}
