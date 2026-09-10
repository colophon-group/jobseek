//go:build !densitybench

package main

import (
	"bytes"
	"context"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/hex"
	"encoding/pem"
	"errors"
	"io"
	"math/big"
	"net"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/framing"
	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/lightpandaadapter"
	"google.golang.org/protobuf/proto"
)

const serviceTestIP = "10.0.0.5"

type serviceTLSFixture struct {
	server runtimeV1ServiceConfig
	client *tls.Config
}

func TestRuntimeV1ServiceHelloIsExactAndBounded(t *testing.T) {
	want := `{"protocol":"jobseek.lightpanda.service/v1","runtime_contract":"crawler.runtime/v1","mode":"b0","capacity":4,"memory_max_bytes":1073741824,"memory_swap_max_bytes":0}`
	if string(runtimeV1ServiceHelloJSON) != want {
		t.Fatalf("hello = %s", runtimeV1ServiceHelloJSON)
	}
	record, err := framing.EncodeRecord(runtimeV1ServiceHelloJSON, runtimeV1HelloFrameLimit)
	if err != nil {
		t.Fatal(err)
	}
	payload, err := framing.DecodeRecord(record, runtimeV1HelloFrameLimit)
	if err != nil || !bytes.Equal(payload, runtimeV1ServiceHelloJSON) {
		t.Fatalf("hello record = %q/%v", payload, err)
	}
}

func TestRuntimeV1ServiceConfigArgumentsAreClosed(t *testing.T) {
	config, err := runtimeV1ServiceConfigFromArgs([]string{
		"--listen", runtimeV1ServiceListenAddress, "--service-ip", serviceTestIP,
		"--tls-cert", "/cert", "--tls-key", "/key",
		"--tls-ca", "/ca", "--tls-ca-sha256", strings.Repeat("1", 64),
		"--client-leaf-sha256", strings.Repeat("2", 64),
		"--client-spki-sha256", strings.Repeat("3", 64),
	})
	if err != nil || config.MemoryMaxPath != defaultMemoryMaxPath ||
		config.MemorySwapMaxPath != defaultMemorySwapMaxPath ||
		config.ListenAddress != runtimeV1ServiceListenAddress || config.ServiceIP != serviceTestIP {
		t.Fatalf("config/error = %#v/%v", config, err)
	}
	for _, args := range [][]string{
		{"--unknown", "value"},
		{"--listen", runtimeV1ServiceListenAddress, "--service-ip", serviceTestIP, "trailing"},
		{"--listen"},
		{"--listen", runtimeV1ServiceListenAddress},
		{"--listen", runtimeV1ServiceListenAddress, "--service-ip"},
	} {
		if _, err := runtimeV1ServiceConfigFromArgs(args); err == nil {
			t.Fatalf("args accepted: %q", args)
		}
	}
}

func TestRuntimeV1ServiceCgroupAttestationIsExact(t *testing.T) {
	directory := t.TempDir()
	memory := filepath.Join(directory, "memory.max")
	swap := filepath.Join(directory, "memory.swap.max")
	write := func(path string, value string) {
		t.Helper()
		if err := os.WriteFile(path, []byte(value), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	write(memory, "1073741824\n")
	write(swap, "0\n")
	if err := attestRuntimeV1ServiceCgroup(memory, swap); err != nil {
		t.Fatal(err)
	}
	for _, test := range []struct {
		name   string
		memory string
		swap   string
	}{
		{name: "unbounded", memory: "max\n", swap: "0\n"},
		{name: "wrong memory", memory: "536870912\n", swap: "0\n"},
		{name: "swap enabled", memory: "1073741824\n", swap: "1073741824\n"},
		{name: "whitespace", memory: " 1073741824\n", swap: "0\n"},
		{name: "noncanonical zero", memory: "1073741824\n", swap: "00\n"},
	} {
		t.Run(test.name, func(t *testing.T) {
			write(memory, test.memory)
			write(swap, test.swap)
			if err := attestRuntimeV1ServiceCgroup(memory, swap); err == nil {
				t.Fatal("bad cgroup limits accepted")
			}
		})
	}
}

func TestRuntimeV1ServiceEndpointConfigurationIsClosed(t *testing.T) {
	if err := validateRuntimeV1ServiceBind(runtimeV1ServiceListenAddress); err != nil {
		t.Fatal(err)
	}
	identity, err := runtimeV1ServiceIdentityIP(serviceTestIP)
	if err != nil || identity.String() != serviceTestIP {
		t.Fatalf("identity/error = %v/%v", identity, err)
	}
	for _, address := range []string{
		"", "10.0.0.5:9443", "127.0.0.1:9443", "0.0.0.0:9444",
		"0.0.0.0:09443", "[::]:9443", "*:9443", "0.0.0.0:9443 ",
	} {
		t.Run("bind/"+address, func(t *testing.T) {
			if err := validateRuntimeV1ServiceBind(address); err == nil {
				t.Fatalf("bind accepted: %q", address)
			}
		})
	}
	for _, value := range []string{
		"", "murmur", "0.0.0.0", "127.0.0.1", "169.254.0.1", "8.8.8.8",
		"10.0.0.05", "::", "::1", "fd00::5", "2001:db8::5", "::ffff:10.0.0.5",
		" 10.0.0.5", "10.0.0.5 ",
	} {
		t.Run("identity/"+value, func(t *testing.T) {
			if _, err := runtimeV1ServiceIdentityIP(value); err == nil {
				t.Fatalf("identity accepted: %q", value)
			}
		})
	}
}

func TestRuntimeV1ServiceConstructionRejectsInvalidEndpointConfiguration(t *testing.T) {
	fixture := newServiceTLSFixture(t)
	executor := runtimeV1ExecutorFunc(func(context.Context, *runtimev1.BrowserExecutionInput) *runtimev1.BrowserResult {
		return serviceSuccess("https://example.test/jobs")
	})
	for _, test := range []struct {
		name  string
		patch func(*runtimeV1ServiceConfig)
	}{
		{name: "nonfixed bind", patch: func(config *runtimeV1ServiceConfig) {
			config.ListenAddress = serviceTestIP + ":9443"
		}},
		{name: "invalid service identity", patch: func(config *runtimeV1ServiceConfig) {
			config.ServiceIP = "0.0.0.0"
		}},
	} {
		t.Run(test.name, func(t *testing.T) {
			config := fixture.server
			test.patch(&config)
			if _, err := newRuntimeV1Service(config, executor); err == nil {
				t.Fatal("invalid endpoint configuration reached service construction")
			}
		})
	}
}

func TestRuntimeV1ServiceRejectsWrongIdentityAndRequiredPins(t *testing.T) {
	fixture := newServiceTLSFixture(t)
	accepted, err := runtimeV1ServiceTLSConfig(fixture.server)
	if err != nil {
		t.Fatal(err)
	}
	if accepted.MinVersion != tls.VersionTLS13 || accepted.MaxVersion != tls.VersionTLS13 ||
		!accepted.SessionTicketsDisabled || accepted.ClientAuth != tls.RequireAndVerifyClientCert ||
		len(accepted.NextProtos) != 1 || accepted.NextProtos[0] != runtimeV1ServiceALPN {
		t.Fatalf("TLS policy = %#v", accepted)
	}
	for _, test := range []struct {
		name  string
		patch func(*runtimeV1ServiceConfig)
	}{
		{name: "missing service IP", patch: func(c *runtimeV1ServiceConfig) { c.ServiceIP = "" }},
		{name: "hostname", patch: func(c *runtimeV1ServiceConfig) { c.ServiceIP = "murmur" }},
		{name: "loopback", patch: func(c *runtimeV1ServiceConfig) { c.ServiceIP = "127.0.0.1" }},
		{name: "wrong server identity", patch: func(c *runtimeV1ServiceConfig) { c.ServiceIP = "10.0.0.6" }},
		{name: "missing CA pin", patch: func(c *runtimeV1ServiceConfig) { c.CASHA256 = "" }},
		{name: "missing leaf pin", patch: func(c *runtimeV1ServiceConfig) { c.ClientLeafSHA256 = "" }},
		{name: "missing SPKI pin", patch: func(c *runtimeV1ServiceConfig) { c.ClientSPKISHA256 = "" }},
		{name: "wrong leaf pin", patch: func(c *runtimeV1ServiceConfig) { c.ClientLeafSHA256 = strings.Repeat("0", 64) }},
	} {
		t.Run(test.name, func(t *testing.T) {
			config := fixture.server
			test.patch(&config)
			tlsConfig, err := runtimeV1ServiceTLSConfig(config)
			if test.name == "wrong leaf pin" {
				if err != nil {
					t.Fatal(err)
				}
				if tlsConfig == nil {
					t.Fatal("missing TLS config")
				}
				return // The wrong peer pin is rejected during its handshake.
			}
			if err == nil {
				t.Fatal("bad identity or pin accepted")
			}
		})
	}
}

func TestRuntimeV1ServiceExactPeerIdentityMatrix(t *testing.T) {
	expectedIP := net.ParseIP(serviceTestIP)
	server := &x509.Certificate{
		IPAddresses:           []net.IP{expectedIP},
		ExtKeyUsage:           []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth},
		BasicConstraintsValid: true,
		Extensions: []pkix.Extension{rawSubjectAlternativeNameTest(
			generalNameTest(0x87, expectedIP.To4()),
		)},
	}
	if !exactServerLeaf(server, expectedIP) {
		t.Fatal("exact server identity rejected")
	}
	for _, invalid := range []*x509.Certificate{
		{IPAddresses: []net.IP{net.ParseIP("10.0.0.6")}, ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth}},
		{IPAddresses: []net.IP{expectedIP}, DNSNames: []string{"murmur"}, ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth}},
		{IPAddresses: []net.IP{expectedIP, expectedIP}, ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth}},
		{IPAddresses: []net.IP{expectedIP}, ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth, x509.ExtKeyUsageClientAuth}},
		{IPAddresses: []net.IP{expectedIP}},
		{IsCA: true, IPAddresses: []net.IP{expectedIP}, ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth}},
	} {
		if exactServerLeaf(invalid, expectedIP) {
			t.Fatalf("invalid server identity accepted: %#v", invalid)
		}
	}

	clientURI, err := url.Parse(runtimeV1ServiceClientURI)
	if err != nil {
		t.Fatal(err)
	}
	client := &x509.Certificate{
		URIs: []*url.URL{clientURI}, ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageClientAuth},
		BasicConstraintsValid: true,
		Extensions: []pkix.Extension{rawSubjectAlternativeNameTest(
			generalNameTest(0x86, []byte(runtimeV1ServiceClientURI)),
		)},
	}
	if !exactClientLeaf(client) {
		t.Fatal("exact client identity rejected")
	}
	serverWithoutBasicConstraints := *server
	serverWithoutBasicConstraints.BasicConstraintsValid = false
	if exactServerLeaf(&serverWithoutBasicConstraints, expectedIP) {
		t.Fatal("server leaf without BasicConstraints accepted")
	}
	clientWithoutBasicConstraints := *client
	clientWithoutBasicConstraints.BasicConstraintsValid = false
	if exactClientLeaf(&clientWithoutBasicConstraints) {
		t.Fatal("client leaf without BasicConstraints accepted")
	}
	wrongURI, _ := url.Parse("spiffe://jobseek/crawler/other")
	for _, invalid := range []*x509.Certificate{
		{URIs: []*url.URL{wrongURI}, ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageClientAuth}},
		{URIs: []*url.URL{clientURI}, DNSNames: []string{"crawler"}, ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageClientAuth}},
		{URIs: []*url.URL{clientURI, clientURI}, ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageClientAuth}},
		{URIs: []*url.URL{clientURI}, ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageClientAuth, x509.ExtKeyUsageServerAuth}},
		{URIs: []*url.URL{clientURI}},
		{IsCA: true, URIs: []*url.URL{clientURI}, ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageClientAuth}},
	} {
		if exactClientLeaf(invalid) {
			t.Fatalf("invalid client identity accepted: %#v", invalid)
		}
	}
}

func TestRuntimeV1ServiceRejectsHiddenSubjectAlternativeNames(t *testing.T) {
	expectedIP := net.ParseIP(serviceTestIP)
	clientURI, err := url.Parse(runtimeV1ServiceClientURI)
	if err != nil {
		t.Fatal(err)
	}
	extras := map[string][]byte{
		"otherName":    {0xa0, 0x00},
		"registeredID": {0x88, 0x02, 0x2a, 0x03},
	}
	for name, extra := range extras {
		t.Run(name+"/server", func(t *testing.T) {
			certificate := &x509.Certificate{
				IPAddresses:           []net.IP{expectedIP},
				ExtKeyUsage:           []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth},
				BasicConstraintsValid: true,
				Extensions: []pkix.Extension{rawSubjectAlternativeNameTest(
					generalNameTest(0x87, expectedIP.To4()), extra,
				)},
			}
			if exactServerLeaf(certificate, expectedIP) {
				t.Fatal("server leaf with hidden GeneralName accepted")
			}
		})
		t.Run(name+"/client", func(t *testing.T) {
			certificate := &x509.Certificate{
				URIs:                  []*url.URL{clientURI},
				ExtKeyUsage:           []x509.ExtKeyUsage{x509.ExtKeyUsageClientAuth},
				BasicConstraintsValid: true,
				Extensions: []pkix.Extension{rawSubjectAlternativeNameTest(
					generalNameTest(0x86, []byte(runtimeV1ServiceClientURI)), extra,
				)},
			}
			if exactClientLeaf(certificate) {
				t.Fatal("client leaf with hidden GeneralName accepted")
			}
		})
	}
}

func TestRuntimeV1ServiceDedicatedCAIsExactlyOnePinnedCertificate(t *testing.T) {
	fixture := newServiceTLSFixture(t)
	contents, err := os.ReadFile(fixture.server.CAPath)
	if err != nil {
		t.Fatal(err)
	}
	duplicate := filepath.Join(t.TempDir(), "duplicate.pem")
	if err := os.WriteFile(duplicate, append(bytes.Clone(contents), contents...), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := loadPinnedCA(duplicate, fixture.server.CASHA256); err == nil {
		t.Fatal("multiple CA certificates accepted")
	}
	if _, err := loadPinnedCA(fixture.server.CAPath, strings.Repeat("0", 64)); err == nil {
		t.Fatal("wrong CA pin accepted")
	}
}

func TestRuntimeV1ServiceOneShotAndEvaluationPreflight(t *testing.T) {
	fixture := newServiceTLSFixture(t)
	var calls atomic.Int32
	runner := runtimeV1AdapterRunnerFunc(func(_ context.Context, bound lightpandaadapter.BoundInput) lightpandaadapter.RunnerOutcome {
		calls.Add(1)
		input := bound.Input()
		status := uint32(200)
		return lightpandaadapter.NewRunnerSuccess(bound, &lightpandaadapter.RawSuccess{
			FinalURL: input.Plan.TargetUrl, Status: &status, HTML: []byte("<html></html>"),
		})
	})
	adapter, err := lightpandaadapter.NewRenderOnly(runner)
	if err != nil {
		t.Fatal(err)
	}
	service, address, stop := startRuntimeV1ServiceTest(t, fixture, adapter)
	defer stop()
	_ = service

	connection := dialRuntimeV1ServiceTest(t, address, fixture.client)
	result := exchangeRuntimeV1ServiceTest(t, connection, bridgeInput("https://example.test/jobs", "", 0))
	if result.GetSuccess() == nil || calls.Load() != 1 {
		t.Fatalf("result/calls = %v/%d", result, calls.Load())
	}

	connection = dialRuntimeV1ServiceTest(t, address, fixture.client)
	result = exchangeRuntimeV1ServiceTest(t, connection, bridgeInput("https://example.test/jobs", "document.title", 64))
	if result.GetUnsupported() == nil || calls.Load() != 1 {
		t.Fatalf("evaluation result/calls = %v/%d", result, calls.Load())
	}
}

func TestRuntimeV1ServiceRejectsMalformedAndPipelinedBeforeExecution(t *testing.T) {
	fixture := newServiceTLSFixture(t)
	var calls atomic.Int32
	executor := runtimeV1ExecutorFunc(func(context.Context, *runtimev1.BrowserExecutionInput) *runtimev1.BrowserResult {
		calls.Add(1)
		return serviceSuccess("https://example.test/jobs")
	})
	_, address, stop := startRuntimeV1ServiceTest(t, fixture, executor)
	defer stop()
	valid := frameRuntimeV1Input(t, bridgeInput("https://example.test/jobs", "", 0)).Bytes()
	tests := []struct {
		name string
		wire []byte
	}{
		{name: "malformed protobuf", wire: append([]byte{1, 0xff}, 0)},
		{name: "oversized", wire: []byte{0x83, 0x80, 0x08}},
		{name: "truncated", wire: []byte{5, 1, 2, 3}},
		{name: "noncanonical terminator", wire: append(bytes.Clone(valid), 0x80, 0)},
		{name: "nonzero second frame", wire: append(append(bytes.Clone(valid), 1), 'x')},
		{name: "pipelined request", wire: append(bytes.Clone(valid), valid...)},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			before := calls.Load()
			connection := dialRuntimeV1ServiceTest(t, address, fixture.client)
			readRuntimeV1HelloTest(t, connection)
			if _, err := connection.Write(test.wire); err != nil {
				t.Fatal(err)
			}
			_ = connection.CloseWrite()
			_, _ = framing.ReadRecord(connection, runtimeV1ResultFrameLimit)
			_ = connection.Close()
			if calls.Load() != before {
				t.Fatalf("bad input executed: before=%d after=%d", before, calls.Load())
			}
		})
	}
}

func TestRuntimeV1ServiceBufferedTrailingBytePreventsExecution(t *testing.T) {
	fixture := newServiceTLSFixture(t)
	var calls atomic.Int32
	executor := runtimeV1ExecutorFunc(func(context.Context, *runtimev1.BrowserExecutionInput) *runtimev1.BrowserResult {
		calls.Add(1)
		return serviceSuccess("https://example.test/jobs")
	})
	_, address, stop := startRuntimeV1ServiceTest(t, fixture, executor)
	defer stop()
	connection := dialRuntimeV1ServiceTest(t, address, fixture.client)
	readRuntimeV1HelloTest(t, connection)
	valid := frameRuntimeV1Input(t, bridgeInput("https://example.test/jobs", "", 0)).Bytes()
	if _, err := connection.Write(append(append(bytes.Clone(valid), 0), 'x')); err != nil {
		t.Fatal(err)
	}
	_ = connection.CloseWrite()
	_, _ = framing.ReadRecord(connection, runtimeV1ResultFrameLimit)
	_ = connection.Close()
	if calls.Load() != 0 {
		t.Fatalf("buffered trailing bytes executed %d times", calls.Load())
	}
}

func TestRuntimeV1ServiceCPlusOneHasNoAdmissionQueue(t *testing.T) {
	fixture := newServiceTLSFixture(t)
	started := make(chan struct{}, runtimeV1ServiceCapacity)
	release := make(chan struct{})
	executor := runtimeV1ExecutorFunc(func(ctx context.Context, input *runtimev1.BrowserExecutionInput) *runtimev1.BrowserResult {
		started <- struct{}{}
		select {
		case <-release:
		case <-ctx.Done():
		}
		return serviceSuccess(input.Plan.TargetUrl)
	})
	_, address, stop := startRuntimeV1ServiceTest(t, fixture, executor)
	defer stop()

	connections := make([]*tls.Conn, 0, runtimeV1ServiceCapacity)
	for range runtimeV1ServiceCapacity {
		connection := dialRuntimeV1ServiceTest(t, address, fixture.client)
		readRuntimeV1HelloTest(t, connection)
		input := frameRuntimeV1Input(t, bridgeInput("https://example.test/jobs", "", 0)).Bytes()
		if _, err := connection.Write(append(input, 0)); err != nil {
			t.Fatal(err)
		}
		connections = append(connections, connection)
	}
	for range runtimeV1ServiceCapacity {
		select {
		case <-started:
		case <-time.After(time.Second):
			t.Fatal("fourth execution did not start")
		}
	}

	raw, err := net.DialTimeout("tcp", address, time.Second)
	if err != nil {
		t.Fatal(err)
	}
	fifth := tls.Client(raw, fixture.client.Clone())
	_ = fifth.SetDeadline(time.Now().Add(time.Second))
	if err := fifth.Handshake(); err == nil {
		t.Fatal("C+1 connection was admitted")
	}
	_ = fifth.Close()
	close(release)
	for _, connection := range connections {
		_, _ = framing.ReadRecord(connection, runtimeV1ResultFrameLimit)
		_ = connection.Close()
	}
}

func TestRuntimeV1ServiceCancellationReachesActiveExecutor(t *testing.T) {
	fixture := newServiceTLSFixture(t)
	started := make(chan struct{})
	cancelled := make(chan struct{})
	executor := runtimeV1ExecutorFunc(func(ctx context.Context, _ *runtimev1.BrowserExecutionInput) *runtimev1.BrowserResult {
		close(started)
		<-ctx.Done()
		close(cancelled)
		return runtimeV1ContextFailure(ctx.Err())
	})
	_, address, stop := startRuntimeV1ServiceTest(t, fixture, executor)
	connection := dialRuntimeV1ServiceTest(t, address, fixture.client)
	readRuntimeV1HelloTest(t, connection)
	input := frameRuntimeV1Input(t, bridgeInput("https://example.test/jobs", "", 0)).Bytes()
	if _, err := connection.Write(append(input, 0)); err != nil {
		t.Fatal(err)
	}
	select {
	case <-started:
	case <-time.After(time.Second):
		t.Fatal("executor did not start")
	}
	stop()
	select {
	case <-cancelled:
	case <-time.After(time.Second):
		t.Fatal("service cancellation did not reach executor")
	}
	_ = connection.Close()
}

func TestRuntimeV1ServiceShutdownClosesEveryPreExecutionState(t *testing.T) {
	tests := []struct {
		name string
		wire func(*testing.T) []byte
	}{
		{name: "stalled after hello", wire: func(*testing.T) []byte { return nil }},
		{name: "partial prefix", wire: func(*testing.T) []byte { return []byte{0x80} }},
		{name: "awaiting terminator", wire: func(t *testing.T) []byte {
			return frameRuntimeV1Input(t, bridgeInput("https://example.test/jobs", "", 0)).Bytes()
		}},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			fixture := newServiceTLSFixture(t)
			var calls atomic.Int32
			executor := runtimeV1ExecutorFunc(func(context.Context, *runtimev1.BrowserExecutionInput) *runtimev1.BrowserResult {
				calls.Add(1)
				return serviceSuccess("https://example.test/jobs")
			})
			_, address, stop := startRuntimeV1ServiceTest(t, fixture, executor)
			connection := dialRuntimeV1ServiceTest(t, address, fixture.client)
			readRuntimeV1HelloTest(t, connection)
			if wire := test.wire(t); len(wire) != 0 {
				if _, err := connection.Write(wire); err != nil {
					t.Fatal(err)
				}
			}

			stopped := make(chan struct{})
			go func() {
				stop()
				close(stopped)
			}()
			select {
			case <-stopped:
			case <-time.After(time.Second):
				t.Fatal("service shutdown waited on a stalled connection")
			}
			var value [1]byte
			if _, err := connection.Read(value[:]); err == nil {
				t.Fatal("stalled connection remained open after shutdown")
			}
			_ = connection.Close()
			if calls.Load() != 0 {
				t.Fatalf("pre-execution shutdown made %d executor calls", calls.Load())
			}
		})
	}
}

func TestRuntimeV1ServicePeerCloseCancelsExecutionAndServiceRemainsSound(t *testing.T) {
	fixture := newServiceTLSFixture(t)
	started := make(chan struct{})
	cleaned := make(chan struct{})
	var calls atomic.Int32
	var cleanups atomic.Int32
	execution, err := newRuntimeV1ServiceExecution(Config{}, func(
		ctx context.Context,
		_ Config,
		task Task,
	) (Result, error) {
		if calls.Add(1) == 1 {
			close(started)
			<-ctx.Done()
			cleanups.Add(1)
			close(cleaned)
			return Result{}, ctx.Err()
		}
		return Result{
			Status: 200, FinalURL: task.URL, HTML: "<html></html>", HTMLPresent: true,
		}, nil
	})
	if err != nil {
		t.Fatal(err)
	}
	_, address, stop := startRuntimeV1ServiceTest(t, fixture, execution)
	defer stop()

	connection := dialRuntimeV1ServiceTest(t, address, fixture.client)
	readRuntimeV1HelloTest(t, connection)
	input := frameRuntimeV1Input(t, bridgeInput("https://example.test/jobs", "", 0)).Bytes()
	if _, err := connection.Write(append(input, 0)); err != nil {
		t.Fatal(err)
	}
	select {
	case <-started:
	case <-time.After(time.Second):
		t.Fatal("executor did not start")
	}
	_ = connection.Close()
	select {
	case <-cleaned:
	case <-time.After(time.Second):
		t.Fatal("peer close did not cancel execution cleanup")
	}
	if cleanups.Load() != 1 {
		t.Fatalf("cleanup calls = %d", cleanups.Load())
	}

	connection = dialRuntimeV1ServiceTest(t, address, fixture.client)
	result := exchangeRuntimeV1ServiceTest(t, connection, bridgeInput("https://example.test/jobs/2", "", 0))
	if result.GetSuccess() == nil || calls.Load() != 2 || cleanups.Load() != 1 {
		t.Fatalf("second result/calls/cleanups = %v/%d/%d", result, calls.Load(), cleanups.Load())
	}
}

func TestRuntimeV1ServiceCleanupUnprovedPoisonsResidentService(t *testing.T) {
	fixture := newServiceTLSFixture(t)
	execution, err := newRuntimeV1ServiceExecution(Config{}, func(
		context.Context,
		Config,
		Task,
	) (Result, error) {
		return Result{}, errors.Join(errors.New("fixture cleanup failure"), errCleanupUnproved)
	})
	if err != nil {
		t.Fatal(err)
	}
	service, err := newRuntimeV1Service(fixture.server, execution)
	if err != nil {
		t.Fatal(err)
	}
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	done := make(chan error, 1)
	go func() { done <- service.serve(ctx, listener) }()
	address := listener.Addr().String()

	stalled := dialRuntimeV1ServiceTest(t, address, fixture.client)
	readRuntimeV1HelloTest(t, stalled)
	poisoning := dialRuntimeV1ServiceTest(t, address, fixture.client)
	readRuntimeV1HelloTest(t, poisoning)
	input := frameRuntimeV1Input(t, bridgeInput("https://example.test/jobs", "", 0)).Bytes()
	if _, err := poisoning.Write(append(input, 0)); err != nil {
		t.Fatal(err)
	}

	select {
	case serveErr := <-done:
		if !errors.Is(serveErr, errCleanupUnproved) {
			t.Fatalf("service error = %v", serveErr)
		}
	case <-time.After(time.Second):
		t.Fatal("cleanup-unproved outcome did not stop the service")
	}
	var value [1]byte
	if _, err := stalled.Read(value[:]); err == nil {
		t.Fatal("cleanup poison did not close another admitted connection")
	}
	if replacement, err := net.DialTimeout("tcp", address, 100*time.Millisecond); err == nil {
		_ = replacement.Close()
		t.Fatal("cleanup poison allowed new admission")
	}
	_ = stalled.Close()
	_ = poisoning.Close()
}

func TestRuntimeV1ServiceRejectsWrongClientPinDuringHandshake(t *testing.T) {
	fixture := newServiceTLSFixture(t)
	fixture.server.ClientLeafSHA256 = strings.Repeat("0", 64)
	_, address, stop := startRuntimeV1ServiceTest(t, fixture, runtimeV1ExecutorFunc(func(context.Context, *runtimev1.BrowserExecutionInput) *runtimev1.BrowserResult {
		t.Fatal("wrong client pin reached executor")
		return nil
	}))
	defer stop()
	raw, err := net.DialTimeout("tcp", address, time.Second)
	if err != nil {
		t.Fatal(err)
	}
	connection := tls.Client(raw, fixture.client.Clone())
	_ = connection.SetDeadline(time.Now().Add(time.Second))
	if err := connection.Handshake(); err == nil {
		// The client can finish before it observes the server's fatal alert. A
		// hello must still be impossible, which is the authoritative admission.
		if _, readErr := framing.ReadRecord(connection, runtimeV1HelloFrameLimit); readErr == nil {
			t.Fatal("wrong client pin received service hello")
		}
	}
	_ = connection.Close()
}

func startRuntimeV1ServiceTest(
	t *testing.T,
	fixture serviceTLSFixture,
	executor runtimeV1Executor,
) (*runtimeV1Service, string, func()) {
	t.Helper()
	service, err := newRuntimeV1Service(fixture.server, executor)
	if err != nil {
		t.Fatal(err)
	}
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() { done <- service.serve(ctx, listener) }()
	var stopped atomic.Bool
	stop := func() {
		if !stopped.CompareAndSwap(false, true) {
			return
		}
		cancel()
		_ = listener.Close()
		select {
		case err := <-done:
			if err != nil {
				t.Errorf("service stop: %v", err)
			}
		case <-time.After(2 * time.Second):
			t.Error("service did not stop")
		}
	}
	return service, listener.Addr().String(), stop
}

func dialRuntimeV1ServiceTest(t *testing.T, address string, config *tls.Config) *tls.Conn {
	t.Helper()
	raw, err := net.DialTimeout("tcp", address, time.Second)
	if err != nil {
		t.Fatal(err)
	}
	connection := tls.Client(raw, config.Clone())
	_ = connection.SetDeadline(time.Now().Add(2 * time.Second))
	if err := connection.Handshake(); err != nil {
		_ = connection.Close()
		t.Fatal(err)
	}
	return connection
}

func readRuntimeV1HelloTest(t *testing.T, connection io.Reader) {
	t.Helper()
	hello, err := framing.ReadRecord(connection, runtimeV1HelloFrameLimit)
	if err != nil || !bytes.Equal(hello, runtimeV1ServiceHelloJSON) {
		t.Fatalf("hello = %q/%v", hello, err)
	}
}

func exchangeRuntimeV1ServiceTest(
	t *testing.T,
	connection *tls.Conn,
	input *runtimev1.BrowserExecutionInput,
) *runtimev1.BrowserResult {
	t.Helper()
	readRuntimeV1HelloTest(t, connection)
	payload, err := proto.MarshalOptions{Deterministic: true}.Marshal(input)
	if err != nil {
		t.Fatal(err)
	}
	record, err := framing.EncodeRecord(payload, runtimeV1InputFrameLimit)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := connection.Write(append(record, 0)); err != nil {
		t.Fatal(err)
	}
	resultPayload, err := framing.ReadRecord(connection, runtimeV1ResultFrameLimit)
	if err != nil {
		t.Fatal(err)
	}
	result := &runtimev1.BrowserResult{}
	if err := proto.Unmarshal(resultPayload, result); err != nil {
		t.Fatal(err)
	}
	var trailing [1]byte
	count, err := connection.Read(trailing[:])
	if count != 0 || !errors.Is(err, io.EOF) {
		t.Fatalf("service did not close after one result: %d/%v", count, err)
	}
	_ = connection.Close()
	return result
}

func serviceSuccess(target string) *runtimev1.BrowserResult {
	status := uint32(200)
	body := []byte("<html></html>")
	digest := sha256.Sum256(body)
	return &runtimev1.BrowserResult{
		ContractVersion: "crawler.runtime/v1",
		Backend:         runtimev1.BrowserBackend_BROWSER_BACKEND_LIGHTPANDA,
		Outcome: &runtimev1.BrowserResult_Success{Success: &runtimev1.BrowserSuccess{
			FinalUrl: target, Status: &status,
			Html: &runtimev1.ChunkManifest{
				Chunks: []*runtimev1.DataChunk{{
					Sequence: 0, SizeBytes: uint64(len(body)), Sha256: hex.EncodeToString(digest[:]),
					Storage: &runtimev1.DataChunk_InlineBody{InlineBody: body},
				}},
				TotalSizeBytes: uint64(len(body)), TotalSha256: hex.EncodeToString(digest[:]), Complete: true,
			},
		}},
	}
}

func newServiceTLSFixture(t *testing.T) serviceTLSFixture {
	t.Helper()
	directory := t.TempDir()
	now := time.Now()
	caKey := mustECDSAKey(t)
	caTemplate := &x509.Certificate{
		SerialNumber: big.NewInt(1), Subject: pkix.Name{CommonName: "lightpanda-test-ca"},
		NotBefore: now.Add(-time.Minute), NotAfter: now.Add(time.Hour),
		IsCA: true, BasicConstraintsValid: true,
		KeyUsage: x509.KeyUsageCertSign | x509.KeyUsageCRLSign,
	}
	caDER := mustCreateCertificate(t, caTemplate, caTemplate, &caKey.PublicKey, caKey)
	ca := mustParseCertificate(t, caDER)

	serverKey := mustECDSAKey(t)
	serverTemplate := &x509.Certificate{
		SerialNumber: big.NewInt(2), Subject: pkix.Name{CommonName: "lightpanda-service"},
		NotBefore: now.Add(-time.Minute), NotAfter: now.Add(time.Hour),
		IPAddresses:           []net.IP{net.ParseIP(serviceTestIP)},
		ExtKeyUsage:           []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth},
		BasicConstraintsValid: true,
	}
	serverDER := mustCreateCertificate(t, serverTemplate, ca, &serverKey.PublicKey, caKey)
	clientKey := mustECDSAKey(t)
	clientURI, err := url.Parse(runtimeV1ServiceClientURI)
	if err != nil {
		t.Fatal(err)
	}
	clientTemplate := &x509.Certificate{
		SerialNumber: big.NewInt(3), Subject: pkix.Name{CommonName: "crawler-lightpanda-b0"},
		NotBefore: now.Add(-time.Minute), NotAfter: now.Add(time.Hour), URIs: []*url.URL{clientURI},
		ExtKeyUsage:           []x509.ExtKeyUsage{x509.ExtKeyUsageClientAuth},
		BasicConstraintsValid: true,
	}
	clientDER := mustCreateCertificate(t, clientTemplate, ca, &clientKey.PublicKey, caKey)

	caPath := filepath.Join(directory, "ca.pem")
	serverPath := filepath.Join(directory, "server.pem")
	serverKeyPath := filepath.Join(directory, "server-key.pem")
	clientPath := filepath.Join(directory, "client.pem")
	clientKeyPath := filepath.Join(directory, "client-key.pem")
	writePEM(t, caPath, "CERTIFICATE", caDER)
	writePEM(t, serverPath, "CERTIFICATE", serverDER)
	writeECKey(t, serverKeyPath, serverKey)
	writePEM(t, clientPath, "CERTIFICATE", clientDER)
	writeECKey(t, clientKeyPath, clientKey)
	memoryPath := filepath.Join(directory, "memory.max")
	swapPath := filepath.Join(directory, "memory.swap.max")
	if err := os.WriteFile(memoryPath, []byte("1073741824\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(swapPath, []byte("0\n"), 0o600); err != nil {
		t.Fatal(err)
	}

	clientPair, err := tls.LoadX509KeyPair(clientPath, clientKeyPath)
	if err != nil {
		t.Fatal(err)
	}
	roots := x509.NewCertPool()
	roots.AddCert(ca)
	clientLeaf := mustParseCertificate(t, clientDER)
	return serviceTLSFixture{
		server: runtimeV1ServiceConfig{
			ListenAddress: runtimeV1ServiceListenAddress, ServiceIP: serviceTestIP,
			CertificatePath: serverPath,
			PrivateKeyPath:  serverKeyPath, CAPath: caPath,
			CASHA256: hexDigest(caDER), ClientLeafSHA256: hexDigest(clientDER),
			ClientSPKISHA256: hexDigest(clientLeaf.RawSubjectPublicKeyInfo),
			MemoryMaxPath:    memoryPath, MemorySwapMaxPath: swapPath,
		},
		client: &tls.Config{
			MinVersion: tls.VersionTLS13, MaxVersion: tls.VersionTLS13,
			Certificates: []tls.Certificate{clientPair}, RootCAs: roots, ServerName: serviceTestIP,
			NextProtos: []string{runtimeV1ServiceALPN}, SessionTicketsDisabled: true,
		},
	}
}

func mustECDSAKey(t *testing.T) *ecdsa.PrivateKey {
	t.Helper()
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	return key
}

func mustCreateCertificate(t *testing.T, template, parent *x509.Certificate, public, private any) []byte {
	t.Helper()
	value, err := x509.CreateCertificate(rand.Reader, template, parent, public, private)
	if err != nil {
		t.Fatal(err)
	}
	return value
}

func mustParseCertificate(t *testing.T, value []byte) *x509.Certificate {
	t.Helper()
	certificate, err := x509.ParseCertificate(value)
	if err != nil {
		t.Fatal(err)
	}
	return certificate
}

func writePEM(t *testing.T, path, blockType string, value []byte) {
	t.Helper()
	if err := os.WriteFile(path, pem.EncodeToMemory(&pem.Block{Type: blockType, Bytes: value}), 0o600); err != nil {
		t.Fatal(err)
	}
}

func writeECKey(t *testing.T, path string, key *ecdsa.PrivateKey) {
	t.Helper()
	value, err := x509.MarshalPKCS8PrivateKey(key)
	if err != nil {
		t.Fatal(err)
	}
	writePEM(t, path, "PRIVATE KEY", value)
}

func hexDigest(value []byte) string {
	digest := sha256.Sum256(value)
	return hex.EncodeToString(digest[:])
}

func generalNameTest(tag byte, value []byte) []byte {
	if len(value) >= 128 {
		panic("test GeneralName is too large")
	}
	encoded := []byte{tag, byte(len(value))}
	return append(encoded, value...)
}

func rawSubjectAlternativeNameTest(names ...[]byte) pkix.Extension {
	var contents []byte
	for _, name := range names {
		contents = append(contents, name...)
	}
	if len(contents) >= 128 {
		panic("test subjectAltName is too large")
	}
	value := []byte{0x30, byte(len(contents))}
	value = append(value, contents...)
	return pkix.Extension{Id: subjectAlternativeNameOID, Value: value}
}
