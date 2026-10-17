package main

import (
	"bytes"
	"context"
	"errors"
	"io"
	"log"
	"net"
	"net/http"
	"strconv"
	"strings"
	"sync"
	"time"
)

func pinnedProxyBridgeAddress(port uint16) string {
	return "http://127.0.0.1:" + strconv.Itoa(int(port))
}

// The browser retains its own HTTP, TLS, cookie and redirect implementation.
// Its only permitted TCP port belongs to this bridge; the parent transport
// validates both DNS sets and sends a numeric CONNECT to the upstream proxy.
type pinnedBrowserProxyBridge struct {
	address   string
	port      uint16
	ctx       context.Context
	cancel    context.CancelFunc
	fail      context.CancelFunc
	transport *http.Transport
	server    *http.Server
	listener  net.Listener
	gate      chan struct{}
	once      sync.Once
	release   sync.Once
	mu        sync.Mutex
	failure   error
	conns     map[net.Conn]struct{}
}

func newPinnedBrowserProxyBridge(parent context.Context, transport *http.Transport, fail context.CancelFunc) (*pinnedBrowserProxyBridge, error) {
	if parent == nil || parent.Err() != nil || transport == nil || transport.Proxy != nil || transport.DialContext == nil || fail == nil {
		return nil, errBrowserProxy
	}
	listener, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		return nil, errBrowserProxy
	}
	ctx, cancel := context.WithCancel(parent)
	bridge := &pinnedBrowserProxyBridge{
		address: "http://" + listener.Addr().String(), port: uint16(listener.Addr().(*net.TCPAddr).Port),
		ctx: ctx, cancel: cancel, fail: fail, transport: transport, listener: listener,
		gate: make(chan struct{}, 16), conns: make(map[net.Conn]struct{}),
	}
	bridge.server = &http.Server{
		Handler: bridge, ReadHeaderTimeout: 2 * time.Second, ReadTimeout: 30 * time.Second,
		IdleTimeout: 30 * time.Second, MaxHeaderBytes: 64 << 10,
		ErrorLog:    log.New(io.Discard, "", 0),
		BaseContext: func(net.Listener) context.Context { return ctx },
		ConnState: func(conn net.Conn, state http.ConnState) {
			bridge.mu.Lock()
			defer bridge.mu.Unlock()
			if state == http.StateNew {
				bridge.conns[conn] = struct{}{}
			} else if state == http.StateClosed {
				delete(bridge.conns, conn)
			}
		},
	}
	context.AfterFunc(ctx, bridge.Stop)
	go func() {
		if err := bridge.server.Serve(listener); err != nil && !errors.Is(err, http.ErrServerClosed) && ctx.Err() == nil {
			bridge.reject()
		}
	}()
	return bridge, nil
}

func (bridge *pinnedBrowserProxyBridge) Err() error {
	bridge.mu.Lock()
	defer bridge.mu.Unlock()
	return bridge.failure
}

func (bridge *pinnedBrowserProxyBridge) reject() { bridge.rejectCause(errBrowserProxy) }

func (bridge *pinnedBrowserProxyBridge) rejectCause(cause error) {
	if bridge.ctx.Err() == nil {
		failure := &navigationTransportError{proxyTransport: true}
		var rejected *browserProxyConnectStatus
		if errors.As(cause, &rejected) && rejected.status == http.StatusProxyAuthRequired {
			failure.proxyAuth = true
		}
		bridge.mu.Lock()
		if bridge.failure == nil {
			bridge.failure = failure
		}
		bridge.mu.Unlock()
		bridge.fail()
	}
}

func (bridge *pinnedBrowserProxyBridge) rejectRequest(w http.ResponseWriter) {
	bridge.rejectRequestCause(w, errBrowserProxy)
}

func (bridge *pinnedBrowserProxyBridge) rejectRequestCause(w http.ResponseWriter, cause error) {
	w.Header().Set("Connection", "close")
	w.WriteHeader(http.StatusBadGateway)
	bridge.rejectCause(cause)
}

// Stop denies further forwarding and closes active sockets, retaining the
// listener's port reservation until the browser process group is proved dead.
func (bridge *pinnedBrowserProxyBridge) Stop() {
	bridge.once.Do(func() {
		bridge.cancel()
		bridge.mu.Lock()
		connections := make([]net.Conn, 0, len(bridge.conns))
		for conn := range bridge.conns {
			connections = append(connections, conn)
		}
		bridge.mu.Unlock()
		for _, conn := range connections {
			conn.Close()
		}
		bridge.transport.CloseIdleConnections()
	})
}

func (bridge *pinnedBrowserProxyBridge) Close() {
	bridge.Stop()
	bridge.release.Do(func() { bridge.server.Close() })
}

func (bridge *pinnedBrowserProxyBridge) ServeHTTP(w http.ResponseWriter, request *http.Request) {
	if bridge.ctx.Err() != nil {
		w.Header().Set("Connection", "close")
		w.WriteHeader(http.StatusServiceUnavailable)
		return
	}
	select {
	case bridge.gate <- struct{}{}:
		defer func() { <-bridge.gate }()
	case <-bridge.ctx.Done():
		w.Header().Set("Connection", "close")
		w.WriteHeader(http.StatusServiceUnavailable)
		return
	default:
		bridge.rejectRequest(w)
		return
	}
	if request.Method == http.MethodConnect {
		bridge.connect(w, request)
		return
	}
	if request.URL == nil || request.URL.Scheme != "http" || request.URL.Host == "" || request.URL.User != nil ||
		request.URL.Opaque != "" || request.URL.Fragment != "" || !strings.EqualFold(request.Host, request.URL.Host) {
		bridge.rejectRequest(w)
		return
	}
	body, err := io.ReadAll(io.LimitReader(request.Body, (512<<10)+1))
	if err != nil || len(body) > 512<<10 {
		bridge.rejectRequest(w)
		return
	}
	outgoing := request.Clone(request.Context())
	outgoing.RequestURI = ""
	outgoing.Body = io.NopCloser(bytes.NewReader(body))
	outgoing.ContentLength = int64(len(body))
	outgoing.TransferEncoding, outgoing.Trailer = nil, nil
	removeProxyHopHeaders(outgoing.Header)
	response, err := bridge.transport.RoundTrip(outgoing)
	if err != nil {
		bridge.rejectRequestCause(w, err)
		return
	}
	defer response.Body.Close()
	content, err := io.ReadAll(io.LimitReader(response.Body, (4<<20)+1))
	if err != nil || len(content) > 4<<20 {
		bridge.rejectRequest(w)
		return
	}
	removeProxyHopHeaders(response.Header)
	for name, values := range response.Header {
		for _, value := range values {
			w.Header().Add(name, value)
		}
	}
	if request.Method != http.MethodHead && response.StatusCode != http.StatusNotModified && response.StatusCode != http.StatusNoContent {
		w.Header().Set("Content-Length", strconv.Itoa(len(content)))
	}
	w.WriteHeader(response.StatusCode)
	if request.Method != http.MethodHead {
		_, _ = w.Write(content)
	}
}

func (bridge *pinnedBrowserProxyBridge) connect(w http.ResponseWriter, request *http.Request) {
	if request.URL == nil || request.URL.Host == "" || request.URL.User != nil || request.URL.Opaque != "" ||
		request.URL.Path != "" || request.URL.RawQuery != "" || request.URL.Fragment != "" || request.Host != request.URL.Host {
		bridge.rejectRequest(w)
		return
	}
	upstream, err := bridge.transport.DialContext(request.Context(), "tcp", request.URL.Host)
	if err != nil {
		bridge.rejectRequestCause(w, err)
		return
	}
	defer upstream.Close()
	hijacker, ok := w.(http.Hijacker)
	if !ok {
		bridge.rejectRequest(w)
		return
	}
	child, buffered, err := hijacker.Hijack()
	if err != nil {
		bridge.reject()
		return
	}
	defer child.Close()
	bridge.mu.Lock()
	bridge.conns[upstream] = struct{}{}
	bridge.mu.Unlock()
	defer func() {
		bridge.mu.Lock()
		delete(bridge.conns, upstream)
		delete(bridge.conns, child)
		bridge.mu.Unlock()
	}()
	stop := context.AfterFunc(bridge.ctx, func() { upstream.Close(); child.Close() })
	defer stop()
	if _, err := buffered.WriteString("HTTP/1.1 200 Connection Established\r\n\r\n"); err != nil || buffered.Flush() != nil {
		bridge.reject()
		return
	}
	finished := make(chan struct{})
	go func() { _, _ = io.Copy(upstream, buffered); upstream.Close(); close(finished) }()
	_, _ = io.Copy(child, upstream)
	child.Close()
	upstream.Close()
	<-finished
}

func removeProxyHopHeaders(headers http.Header) {
	for _, value := range headers.Values("Connection") {
		for _, name := range strings.Split(value, ",") {
			headers.Del(strings.TrimSpace(name))
		}
	}
	for _, name := range []string{"Connection", "Proxy-Connection", "Keep-Alive", "Proxy-Authenticate", "Proxy-Authorization", "TE", "Trailer", "Transfer-Encoding", "Upgrade"} {
		headers.Del(name)
	}
}
