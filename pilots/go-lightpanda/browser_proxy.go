package main

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net"
	"net/netip"
	"net/url"
	"os"
	"strings"
	"sync"
	"syscall"

	"github.com/chromedp/cdproto/cdp"
	"github.com/chromedp/cdproto/fetch"
	"github.com/chromedp/cdproto/target"
	"github.com/chromedp/chromedp"
	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/browserproxy"
)

const configuredBrowserProxyFile = "/run/lightpanda/proxies.json"

var errBrowserProxy = errors.New("configured browser proxy unavailable")

func parseBrowserProxies(raw []byte) (map[string]browserproxy.Endpoint, error) {
	if len(raw) == 0 || len(raw) > 1<<20 {
		return nil, errBrowserProxy
	}
	var entries []string
	if json.Unmarshal(raw, &entries) != nil || len(entries) < 1 || len(entries) > 64 {
		return nil, errBrowserProxy
	}
	result := make(map[string]browserproxy.Endpoint, len(entries))
	for _, raw := range entries {
		endpoint, err := browserproxy.Parse(raw)
		if err != nil {
			return nil, errBrowserProxy
		}
		if _, exists := result[endpoint.Reference()]; exists {
			return nil, errBrowserProxy
		}
		result[endpoint.Reference()] = endpoint
	}
	return result, nil
}

// The controller alone reads this optional protected deployment file. Nothing
// from a request can select a file, endpoint, credential or child environment.
func readBrowserProxies(path string) (map[string]browserproxy.Endpoint, error) {
	f, err := os.OpenFile(path, os.O_RDONLY|syscall.O_NOFOLLOW|syscall.O_NONBLOCK, 0)
	if os.IsNotExist(err) {
		return nil, nil
	}
	if err != nil {
		return nil, errBrowserProxy
	}
	defer f.Close()
	info, err := f.Stat()
	if err != nil || !info.Mode().IsRegular() || info.Mode().Perm() != 0400 || info.Size() < 1 || info.Size() > 1<<20 {
		return nil, errBrowserProxy
	}
	metadata, ok := info.Sys().(*syscall.Stat_t)
	if !ok || metadata.Uid != 10001 || metadata.Nlink != 1 {
		return nil, errBrowserProxy
	}
	raw, err := io.ReadAll(io.LimitReader(f, (1<<20)+1))
	if err != nil {
		return nil, errBrowserProxy
	}
	return parseBrowserProxies(raw)
}

// Proxy credentials configure the private child context through local CDP.
// They never appear in argv, origin headers, external requests or child env.
func configuredProxyContext(endpoint browserproxy.Endpoint) chromedp.ContextOption {
	return chromedp.WithNewBrowserContext(func(params *target.CreateBrowserContextParams) *target.CreateBrowserContextParams {
		u, _ := url.Parse(endpoint.Address())
		username, password := endpoint.Credentials()
		if username != "" {
			u.User = url.UserPassword(username, password)
		}
		return params.WithProxyServer(u.String()).WithDisposeOnDetach(false)
	})
}

// A bounded single worker handles Fetch events outside ListenTarget's callback.
func installBrowserProxyAuthentication(target context.Context, policy EgressPolicy, cancel context.CancelFunc) (func(), error) {
	if policy.validate() != nil {
		return nil, errBrowserProxy
	}
	ctx, stop := context.WithCancel(target)
	events := make(chan any, 32)
	done := make(chan struct{})
	var once sync.Once
	fail := func() { once.Do(cancel) }
	chromedp.ListenTarget(ctx, func(event any) {
		switch event.(type) {
		case *fetch.EventRequestPaused, *fetch.EventAuthRequired:
		default:
			return
		}
		select {
		case events <- event:
		case <-ctx.Done():
		default:
			fail()
		}
	})
	go func() {
		defer close(done)
		for {
			select {
			case <-ctx.Done():
				return
			case event := <-events:
				executor := cdp.WithExecutor(ctx, chromedp.FromContext(target).Target)
				var err error
				switch event := event.(type) {
				case *fetch.EventRequestPaused:
					if event.Request == nil || !browserProxyDestinationAllowed(ctx, policy, event.Request.URL) {
						fail()
						return
					}
					err = fetch.ContinueRequest(event.RequestID).Do(executor)
				case *fetch.EventAuthRequired:
					// The context already owns proxy credentials. Never offer them
					// to a server challenge or retry an unsuccessful proxy challenge.
					response := &fetch.AuthChallengeResponse{Response: fetch.AuthChallengeResponseResponseCancelAuth}
					err = fetch.ContinueWithAuth(event.RequestID, response).Do(executor)
				}
				if err != nil && ctx.Err() == nil {
					fail()
					return
				}
			}
		}
	}()
	if err := chromedp.Run(target, fetch.Enable().WithHandleAuthRequests(true)); err != nil {
		stop()
		<-done
		return nil, errBrowserProxy
	}
	return func() { stop(); <-done }, nil
}

// Lightpanda's socket CIDR filter sees the proxy address when proxying. Check
// every intercepted URL, including redirects and subresources, before release.
// This does not pin the remote proxy's DNS answer: proxy profiles must remain
// unadmitted until their upstream destination enforcement is qualified too.
func browserProxyDestinationAllowed(ctx context.Context, policy EgressPolicy, raw string) bool {
	u, err := url.Parse(raw)
	if err != nil || u.User != nil || u.Hostname() == "" || (u.Scheme != "http" && u.Scheme != "https") {
		return false
	}
	ips, err := net.DefaultResolver.LookupNetIP(ctx, "ip", u.Hostname())
	if err != nil {
		return false
	}
	prefixes := make([]netip.Prefix, 0, maxEgressCIDRCount)
	for _, raw := range strings.Split(policy.blockCIDRs, ",") {
		prefix, err := netip.ParsePrefix(raw)
		if err != nil {
			return false
		}
		prefixes = append(prefixes, prefix)
	}
	return replayHTTPAddressesAllowed(prefixes, ips)
}
