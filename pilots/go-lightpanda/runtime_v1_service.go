//go:build !densitybench

package main

import (
	"bufio"
	"bytes"
	"context"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"encoding/asn1"
	"encoding/hex"
	"encoding/pem"
	"errors"
	"flag"
	"io"
	"net"
	"net/url"
	"os"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/framing"
	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/lightpandaadapter"
	"google.golang.org/protobuf/proto"
)

const (
	runtimeV1ServiceFlag          = "--runtime-v1-service"
	runtimeV1ServiceProtocol      = "jobseek.lightpanda.service/v1"
	runtimeV1ServiceALPN          = "jobseek-lightpanda-b0/1"
	runtimeV1ServiceClientURI     = "spiffe://jobseek/crawler/lightpanda-b0"
	runtimeV1ServiceListenAddress = "0.0.0.0:9443"
	runtimeV1ServiceCapacity      = 4
	runtimeV1ServiceMemoryMax     = uint64(1_073_741_824)
	runtimeV1ServiceMemorySwapMax = uint64(0)
	runtimeV1HelloFrameLimit      = uint64(512)
	runtimeV1TerminatorFrameLimit = uint64(1)
	maxPEMFileBytes               = int64(128 * 1024)
	serviceHandshakeTimeout       = 10 * time.Second
	serviceConnectionTimeout      = 135 * time.Second
	defaultMemoryMaxPath          = "/sys/fs/cgroup/memory.max"
	defaultMemorySwapMaxPath      = "/sys/fs/cgroup/memory.swap.max"
)

var runtimeV1ServiceHelloJSON = []byte(
	`{"protocol":"jobseek.lightpanda.service/v1","runtime_contract":"crawler.runtime/v1","mode":"b0","capacity":4,"memory_max_bytes":1073741824,"memory_swap_max_bytes":0}`,
)

var subjectAlternativeNameOID = asn1.ObjectIdentifier{2, 5, 29, 17}

type runtimeV1ServiceConfig struct {
	ListenAddress       string
	ServiceIP           string
	CertificatePath     string
	PrivateKeyPath      string
	CAPath              string
	CASHA256            string
	ClientLeafSHA256    string
	ClientSPKISHA256    string
	MemoryMaxPath       string
	MemorySwapMaxPath   string
	serviceEgressPolicy runtimeV1ServiceEgressPolicy
}

type repeatedDeploymentCIDRFlag []string

func (values *repeatedDeploymentCIDRFlag) String() string {
	if values == nil || len(*values) == 0 {
		return ""
	}
	return "<redacted>"
}

func (values *repeatedDeploymentCIDRFlag) Set(value string) error {
	*values = append(*values, value)
	return nil
}

type runtimeV1Service struct {
	executor    runtimeV1Executor
	tlsConfig   *tls.Config
	slots       chan struct{}
	hello       []byte
	lifecycleMu sync.Mutex
	listener    net.Listener
	connections map[net.Conn]context.CancelFunc
	stopping    bool
	stopErr     error
	wait        sync.WaitGroup
}

// runtimeV1ServiceExecution keeps the fatal cleanup signal out of the wire
// result. The adapter still returns its closed INTERNAL failure, while the
// resident service independently poisons itself and exits for replacement.
type runtimeV1ServiceExecution struct {
	executor     runtimeV1Executor
	egressPolicy EgressPolicy
	fatalMu      sync.Mutex
	fatal        func(error)
	fatalErr     error
}

func newRuntimeV1ServiceExecution(config Config, run taskRunner) (*runtimeV1ServiceExecution, error) {
	execution := &runtimeV1ServiceExecution{egressPolicy: config.EgressPolicy}
	if run == nil {
		run = runTask
	}
	monitoredRun := func(ctx context.Context, config Config, task Task) (Result, error) {
		result, err := run(ctx, config, task)
		if errors.Is(err, errCleanupUnproved) {
			execution.reportFatal(errCleanupUnproved)
		}
		return result, err
	}
	adapter, err := lightpandaadapter.NewRenderOnly(runtimeV1Runner{config: config, run: monitoredRun})
	if err != nil {
		return nil, err
	}
	execution.executor = adapter
	return execution, nil
}

func (execution *runtimeV1ServiceExecution) Execute(
	ctx context.Context,
	input *runtimev1.BrowserExecutionInput,
) *runtimev1.BrowserResult {
	return execution.executor.Execute(ctx, input)
}

func (execution *runtimeV1ServiceExecution) bindFatal(handler func(error)) error {
	if handler == nil {
		return errors.New("runtime-v1 service fatal handler is required")
	}
	execution.fatalMu.Lock()
	if execution.fatal != nil {
		execution.fatalMu.Unlock()
		return errors.New("runtime-v1 service fatal handler is already bound")
	}
	execution.fatal = handler
	pending := execution.fatalErr
	execution.fatalMu.Unlock()
	if pending != nil {
		handler(pending)
	}
	return nil
}

func (execution *runtimeV1ServiceExecution) reportFatal(err error) {
	execution.fatalMu.Lock()
	if execution.fatalErr != nil {
		execution.fatalMu.Unlock()
		return
	}
	execution.fatalErr = err
	handler := execution.fatal
	execution.fatalMu.Unlock()
	if handler != nil {
		handler(err)
	}
}

func runtimeV1ServiceConfigFromArgs(args []string) (runtimeV1ServiceConfig, error) {
	set := flag.NewFlagSet("go-lightpanda-service", flag.ContinueOnError)
	set.SetOutput(io.Discard)
	config := runtimeV1ServiceConfig{}
	deploymentDenyCIDRs := repeatedDeploymentCIDRFlag{}
	set.StringVar(&config.ListenAddress, "listen", "", "fixed container-local TCP bind")
	set.StringVar(&config.ServiceIP, "service-ip", "", "private literal service identity IP")
	set.Var(&deploymentDenyCIDRs, "deployment-deny-cidr", "trusted deployment address CIDR to deny (repeatable)")
	set.StringVar(&config.CertificatePath, "tls-cert", "", "server certificate PEM")
	set.StringVar(&config.PrivateKeyPath, "tls-key", "", "server private key PEM")
	set.StringVar(&config.CAPath, "tls-ca", "", "dedicated service CA certificate PEM")
	set.StringVar(&config.CASHA256, "tls-ca-sha256", "", "dedicated service CA DER SHA-256")
	set.StringVar(&config.ClientLeafSHA256, "client-leaf-sha256", "", "required client leaf DER SHA-256")
	set.StringVar(&config.ClientSPKISHA256, "client-spki-sha256", "", "required client SPKI SHA-256")
	if err := set.Parse(args); err != nil || set.NArg() != 0 || config.ServiceIP == "" {
		return runtimeV1ServiceConfig{}, errors.New("invalid service arguments")
	}
	servicePolicy, err := newRuntimeV1ServiceEgressPolicy(deploymentDenyCIDRs)
	if err != nil {
		return runtimeV1ServiceConfig{}, errors.New("invalid service arguments")
	}
	config.serviceEgressPolicy = servicePolicy
	config.MemoryMaxPath = defaultMemoryMaxPath
	config.MemorySwapMaxPath = defaultMemorySwapMaxPath
	return config, nil
}

func newRuntimeV1Service(
	config runtimeV1ServiceConfig,
	execution *runtimeV1ServiceExecution,
) (*runtimeV1Service, error) {
	if err := config.serviceEgressPolicy.validate(); err != nil {
		return nil, err
	}
	if execution == nil || execution.executor == nil {
		return nil, errors.New("runtime-v1 service requires a bound execution")
	}
	if err := execution.egressPolicy.validate(); err != nil {
		return nil, errors.New("runtime-v1 service execution has an invalid egress policy")
	}
	if execution.egressPolicy.blockCIDRs != config.serviceEgressPolicy.egressPolicy.blockCIDRs {
		return nil, errors.New("runtime-v1 service execution egress policy does not match the qualified service policy")
	}
	if err := validateRuntimeV1ServiceBind(config.ListenAddress); err != nil {
		return nil, err
	}
	if _, err := runtimeV1ServiceIdentityIP(config.ServiceIP); err != nil {
		return nil, err
	}
	if err := attestRuntimeV1ServiceCgroup(config.MemoryMaxPath, config.MemorySwapMaxPath); err != nil {
		return nil, err
	}
	tlsConfig, err := runtimeV1ServiceTLSConfig(config)
	if err != nil {
		return nil, err
	}
	hello, err := framing.EncodeRecord(runtimeV1ServiceHelloJSON, runtimeV1HelloFrameLimit)
	if err != nil {
		return nil, errors.New("runtime-v1 service hello is invalid")
	}
	service := &runtimeV1Service{
		executor: execution, tlsConfig: tlsConfig,
		slots: make(chan struct{}, runtimeV1ServiceCapacity), hello: hello,
		connections: make(map[net.Conn]context.CancelFunc, runtimeV1ServiceCapacity),
	}
	if err := execution.bindFatal(service.stop); err != nil {
		return nil, err
	}
	return service, nil
}

func runRuntimeV1Service(
	ctx context.Context,
	config runtimeV1ServiceConfig,
	execution *runtimeV1ServiceExecution,
) error {
	service, err := newRuntimeV1Service(config, execution)
	if err != nil {
		return err
	}
	listener, err := net.Listen("tcp", config.ListenAddress)
	if err != nil {
		return errors.New("runtime-v1 service could not listen")
	}
	return service.serve(ctx, listener)
}

func (service *runtimeV1Service) serve(ctx context.Context, listener net.Listener) error {
	if ctx == nil {
		ctx = context.Background()
	}
	if service == nil || service.executor == nil || service.tlsConfig == nil || listener == nil {
		return errors.New("runtime-v1 service is not initialized")
	}
	if err := service.start(listener); err != nil {
		_ = listener.Close()
		return err
	}
	watcherStop := make(chan struct{})
	watcherDone := make(chan struct{})
	go func() {
		defer close(watcherDone)
		select {
		case <-ctx.Done():
			service.stop(nil)
		case <-watcherStop:
		}
	}()

	var serveErr error
	for {
		connection, err := listener.Accept()
		if err != nil {
			if ctx.Err() == nil && service.failure() == nil {
				serveErr = errors.New("runtime-v1 service accept failed")
			}
			break
		}
		select {
		case service.slots <- struct{}{}:
			connectionContext, admitted := service.admit(ctx, connection)
			if !admitted {
				<-service.slots
				_ = connection.Close()
				continue
			}
			go func() {
				defer func() {
					service.release(connection)
					<-service.slots
				}()
				service.handleConnection(connectionContext, connection)
			}()
		default:
			_ = connection.Close()
		}
	}
	service.stop(nil)
	service.wait.Wait()
	close(watcherStop)
	<-watcherDone
	if fatalErr := service.failure(); fatalErr != nil {
		return fatalErr
	}
	return serveErr
}

func (service *runtimeV1Service) start(listener net.Listener) error {
	service.lifecycleMu.Lock()
	defer service.lifecycleMu.Unlock()
	if service.stopping || service.listener != nil {
		return errors.New("runtime-v1 service cannot be started")
	}
	service.listener = listener
	return nil
}

func (service *runtimeV1Service) admit(
	ctx context.Context,
	connection net.Conn,
) (context.Context, bool) {
	connectionContext, cancel := context.WithTimeout(ctx, serviceConnectionTimeout)
	service.lifecycleMu.Lock()
	if service.stopping || ctx.Err() != nil {
		service.lifecycleMu.Unlock()
		cancel()
		return nil, false
	}
	service.connections[connection] = cancel
	// stop sets stopping while holding lifecycleMu before it calls Wait, so no
	// Add can race with or follow the first Wait.
	service.wait.Add(1)
	service.lifecycleMu.Unlock()
	return connectionContext, true
}

func (service *runtimeV1Service) release(connection net.Conn) {
	service.lifecycleMu.Lock()
	cancel := service.connections[connection]
	delete(service.connections, connection)
	service.lifecycleMu.Unlock()
	if cancel != nil {
		cancel()
	}
	_ = connection.Close()
	service.wait.Done()
}

func (service *runtimeV1Service) stop(cause error) {
	service.lifecycleMu.Lock()
	service.stopping = true
	if cause != nil && service.stopErr == nil {
		service.stopErr = cause
	}
	listener := service.listener
	connections := make(map[net.Conn]context.CancelFunc, len(service.connections))
	for connection, cancel := range service.connections {
		connections[connection] = cancel
	}
	service.lifecycleMu.Unlock()

	if listener != nil {
		_ = listener.Close()
	}
	for connection, cancel := range connections {
		cancel()
		_ = connection.Close()
	}
}

func (service *runtimeV1Service) failure() error {
	service.lifecycleMu.Lock()
	defer service.lifecycleMu.Unlock()
	return service.stopErr
}

func (service *runtimeV1Service) handleConnection(
	connectionContext context.Context,
	connection net.Conn,
) {
	defer connection.Close()
	_ = connection.SetDeadline(time.Now().Add(serviceConnectionTimeout))
	tlsConnection := tls.Server(connection, service.tlsConfig)
	defer tlsConnection.Close()
	handshakeContext, cancelHandshake := context.WithTimeout(connectionContext, serviceHandshakeTimeout)
	err := tlsConnection.HandshakeContext(handshakeContext)
	cancelHandshake()
	if err != nil {
		return
	}
	if _, err := writeFull(tlsConnection, service.hello); err != nil {
		return
	}

	reader := bufio.NewReader(tlsConnection)
	payload, err := framing.ReadRecord(reader, runtimeV1InputFrameLimit)
	if err != nil {
		_ = writeRuntimeV1Result(tlsConnection, sanitizeRuntimeV1Result(runtimeV1ReadFailure(err)))
		return
	}
	terminator, err := framing.ReadRecord(reader, runtimeV1TerminatorFrameLimit)
	if err != nil || len(terminator) != 0 || reader.Buffered() != 0 {
		_ = writeRuntimeV1Result(tlsConnection, runtimeV1Failure(
			runtimev1.ErrorCode_ERROR_CODE_INVALID_CONFIG,
			runtimev1.ErrorDisposition_ERROR_DISPOSITION_INVALID_CONFIG_POLICY,
		))
		return
	}

	request := &runtimev1.BrowserExecutionInput{}
	if len(payload) == 0 || proto.Unmarshal(payload, request) != nil {
		_ = writeRuntimeV1Result(tlsConnection, runtimeV1Failure(
			runtimev1.ErrorCode_ERROR_CODE_INVALID_CONFIG,
			runtimev1.ErrorDisposition_ERROR_DISPOSITION_INVALID_CONFIG_POLICY,
		))
		return
	}
	executionContext, cancelExecution := context.WithCancel(connectionContext)
	readDone := make(chan runtimeV1PostMarkerRead, 1)
	go watchRuntimeV1PostMarker(reader, cancelExecution, readDone)
	result := sanitizeRuntimeV1Result(service.executor.Execute(executionContext, request))
	// A TLS stream has no safe application-level half-close. Interrupt the sole
	// post-marker reader, join it, and only then write the one response.
	_ = tlsConnection.SetReadDeadline(time.Now())
	postMarker := <-readDone
	cancelExecution()
	if postMarker.peerGone {
		return
	}
	if postMarker.trailing {
		result = runtimeV1Failure(
			runtimev1.ErrorCode_ERROR_CODE_INVALID_CONFIG,
			runtimev1.ErrorDisposition_ERROR_DISPOSITION_INVALID_CONFIG_POLICY,
		)
	}
	_ = writeRuntimeV1Result(tlsConnection, result)
}

type runtimeV1PostMarkerRead struct {
	peerGone bool
	trailing bool
}

func watchRuntimeV1PostMarker(
	reader io.Reader,
	cancel context.CancelFunc,
	done chan<- runtimeV1PostMarkerRead,
) {
	var trailing [1]byte
	count, err := reader.Read(trailing[:])
	result := runtimeV1PostMarkerRead{trailing: count != 0}
	if err != nil {
		var networkError net.Error
		result.peerGone = !errors.As(err, &networkError) || !networkError.Timeout()
	}
	// Peer EOF/error or any delayed trailing byte revokes execution authority.
	// The local read-deadline interrupt runs after Execute has already returned,
	// so cancelling in that case is harmless and keeps the watcher branchless.
	cancel()
	done <- result
}

func writeFull(writer io.Writer, value []byte) (int, error) {
	written := 0
	for written < len(value) {
		count, err := writer.Write(value[written:])
		if count < 0 || count > len(value)-written || (count == 0 && err == nil) {
			return written, io.ErrShortWrite
		}
		written += count
		if err != nil {
			return written, err
		}
	}
	return written, nil
}

func attestRuntimeV1ServiceCgroup(memoryMaxPath string, memorySwapMaxPath string) error {
	if memoryMaxPath == "" || memorySwapMaxPath == "" {
		return errors.New("runtime-v1 service cgroup paths are required")
	}
	memoryMax, err := readCgroupLimit(memoryMaxPath)
	if err != nil || memoryMax != runtimeV1ServiceMemoryMax {
		return errors.New("runtime-v1 service requires memory.max=1073741824")
	}
	memorySwapMax, err := readCgroupLimit(memorySwapMaxPath)
	if err != nil || memorySwapMax != runtimeV1ServiceMemorySwapMax {
		return errors.New("runtime-v1 service requires memory.swap.max=0")
	}
	return nil
}

func readCgroupLimit(path string) (uint64, error) {
	value, err := os.ReadFile(path)
	if err != nil || len(value) == 0 || len(value) > 32 {
		return 0, errors.New("invalid cgroup limit")
	}
	trimmed := strings.TrimSuffix(string(value), "\n")
	if trimmed == "" || strings.TrimSpace(trimmed) != trimmed || trimmed == "max" ||
		strings.HasPrefix(trimmed, "+") || (len(trimmed) > 1 && trimmed[0] == '0') {
		return 0, errors.New("invalid cgroup limit")
	}
	parsed, err := strconv.ParseUint(trimmed, 10, 64)
	if err != nil {
		return 0, errors.New("invalid cgroup limit")
	}
	return parsed, nil
}

func runtimeV1ServiceTLSConfig(config runtimeV1ServiceConfig) (*tls.Config, error) {
	ip, err := runtimeV1ServiceIdentityIP(config.ServiceIP)
	if err != nil {
		return nil, err
	}
	ca, err := loadPinnedCA(config.CAPath, config.CASHA256)
	if err != nil {
		return nil, err
	}
	for _, path := range []string{config.CertificatePath, config.PrivateKeyPath} {
		if err := requireBoundedRegularFile(path); err != nil {
			return nil, err
		}
	}
	pair, err := tls.LoadX509KeyPair(config.CertificatePath, config.PrivateKeyPath)
	if err != nil || len(pair.Certificate) != 1 {
		return nil, errors.New("runtime-v1 service requires one server leaf certificate")
	}
	leaf, err := x509.ParseCertificate(pair.Certificate[0])
	if err != nil || !exactServerLeaf(leaf, ip) {
		return nil, errors.New("runtime-v1 service server certificate identity is invalid")
	}
	roots := x509.NewCertPool()
	roots.AddCert(ca)
	if _, err := leaf.Verify(x509.VerifyOptions{
		Roots: roots, DNSName: ip.String(), KeyUsages: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth},
	}); err != nil {
		return nil, errors.New("runtime-v1 service server certificate is not issued by the dedicated CA")
	}
	pair.Leaf = leaf

	clientLeafPin, err := requiredSHA256(config.ClientLeafSHA256)
	if err != nil {
		return nil, errors.New("runtime-v1 service client leaf pin is invalid")
	}
	clientSPKIPin, err := requiredSHA256(config.ClientSPKISHA256)
	if err != nil {
		return nil, errors.New("runtime-v1 service client SPKI pin is invalid")
	}
	clientRoots := x509.NewCertPool()
	clientRoots.AddCert(ca)
	return &tls.Config{
		MinVersion:             tls.VersionTLS13,
		MaxVersion:             tls.VersionTLS13,
		Certificates:           []tls.Certificate{pair},
		ClientAuth:             tls.RequireAndVerifyClientCert,
		ClientCAs:              clientRoots,
		NextProtos:             []string{runtimeV1ServiceALPN},
		SessionTicketsDisabled: true,
		VerifyConnection: func(state tls.ConnectionState) error {
			if state.Version != tls.VersionTLS13 || state.NegotiatedProtocol != runtimeV1ServiceALPN ||
				len(state.PeerCertificates) != 1 || len(state.VerifiedChains) != 1 {
				return errors.New("runtime-v1 service client TLS identity is invalid")
			}
			client := state.PeerCertificates[0]
			if !exactClientLeaf(client) ||
				!bytes.Equal(clientLeafPin, digest(client.Raw)) ||
				!bytes.Equal(clientSPKIPin, digest(client.RawSubjectPublicKeyInfo)) {
				return errors.New("runtime-v1 service client TLS identity is invalid")
			}
			return nil
		},
	}, nil
}

func validateRuntimeV1ServiceBind(address string) error {
	if address != runtimeV1ServiceListenAddress {
		return errors.New("runtime-v1 service listen address must be exactly 0.0.0.0:9443")
	}
	return nil
}

func runtimeV1ServiceIdentityIP(value string) (net.IP, error) {
	ip := net.ParseIP(value)
	if ip == nil || ip.To4() == nil || !ip.IsPrivate() || ip.IsLoopback() || ip.IsUnspecified() || value != ip.String() {
		return nil, errors.New("runtime-v1 service identity must be a canonical private IPv4 address")
	}
	return ip, nil
}

func requireBoundedRegularFile(path string) error {
	if path == "" {
		return errors.New("runtime-v1 service credential path is required")
	}
	info, err := os.Stat(path)
	if err != nil || !info.Mode().IsRegular() || info.Size() <= 0 || info.Size() > maxPEMFileBytes {
		return errors.New("runtime-v1 service credential file is invalid")
	}
	return nil
}

func loadPinnedCA(path string, expectedSHA256 string) (*x509.Certificate, error) {
	if err := requireBoundedRegularFile(path); err != nil {
		return nil, err
	}
	expected, err := requiredSHA256(expectedSHA256)
	if err != nil {
		return nil, errors.New("runtime-v1 service CA pin is invalid")
	}
	contents, err := os.ReadFile(path)
	if err != nil {
		return nil, errors.New("runtime-v1 service CA could not be read")
	}
	block, rest := pem.Decode(contents)
	if block == nil || block.Type != "CERTIFICATE" || len(bytes.TrimSpace(rest)) != 0 {
		return nil, errors.New("runtime-v1 service requires one dedicated CA certificate")
	}
	certificate, err := x509.ParseCertificate(block.Bytes)
	if err != nil || !certificate.IsCA || !certificate.BasicConstraintsValid ||
		!bytes.Equal(expected, digest(certificate.Raw)) {
		return nil, errors.New("runtime-v1 service dedicated CA is invalid")
	}
	return certificate, nil
}

func exactServerLeaf(certificate *x509.Certificate, expectedIP net.IP) bool {
	if certificate == nil || expectedIP == nil {
		return false
	}
	rawIP := expectedIP.To4()
	if rawIP == nil {
		rawIP = expectedIP.To16()
	}
	return certificate.BasicConstraintsValid && !certificate.IsCA && len(certificate.IPAddresses) == 1 &&
		certificate.IPAddresses[0].Equal(expectedIP) && len(certificate.DNSNames) == 0 &&
		len(certificate.URIs) == 0 && len(certificate.EmailAddresses) == 0 &&
		exactRawSubjectAlternativeName(certificate, 7, rawIP) &&
		exactExtendedKeyUsage(certificate, x509.ExtKeyUsageServerAuth)
}

func exactClientLeaf(certificate *x509.Certificate) bool {
	if certificate == nil || !certificate.BasicConstraintsValid || certificate.IsCA ||
		len(certificate.URIs) != 1 || len(certificate.IPAddresses) != 0 ||
		len(certificate.DNSNames) != 0 || len(certificate.EmailAddresses) != 0 ||
		!exactExtendedKeyUsage(certificate, x509.ExtKeyUsageClientAuth) {
		return false
	}
	expected, _ := url.Parse(runtimeV1ServiceClientURI)
	return certificate.URIs[0].String() == expected.String() &&
		exactRawSubjectAlternativeName(certificate, 6, []byte(runtimeV1ServiceClientURI))
}

// exactRawSubjectAlternativeName closes the gap in crypto/x509's convenient
// SAN slices, which intentionally omit unsupported GeneralName alternatives.
// This service accepts one primitive IP or URI GeneralName and no other tag.
func exactRawSubjectAlternativeName(certificate *x509.Certificate, tag int, value []byte) bool {
	var extensionValue []byte
	found := false
	for _, extension := range certificate.Extensions {
		if !extension.Id.Equal(subjectAlternativeNameOID) {
			continue
		}
		if found {
			return false
		}
		found = true
		extensionValue = extension.Value
	}
	if !found {
		return false
	}
	var sequence asn1.RawValue
	rest, err := asn1.Unmarshal(extensionValue, &sequence)
	if err != nil || len(rest) != 0 || sequence.Class != asn1.ClassUniversal ||
		sequence.Tag != asn1.TagSequence || !sequence.IsCompound {
		return false
	}
	var name asn1.RawValue
	rest, err = asn1.Unmarshal(sequence.Bytes, &name)
	return err == nil && len(rest) == 0 && name.Class == asn1.ClassContextSpecific &&
		name.Tag == tag && !name.IsCompound && bytes.Equal(name.Bytes, value)
}

func exactExtendedKeyUsage(certificate *x509.Certificate, expected x509.ExtKeyUsage) bool {
	return len(certificate.ExtKeyUsage) == 1 && certificate.ExtKeyUsage[0] == expected &&
		len(certificate.UnknownExtKeyUsage) == 0
}

func digest(value []byte) []byte {
	result := sha256.Sum256(value)
	return result[:]
}

func requiredSHA256(value string) ([]byte, error) {
	if len(value) != sha256.Size*2 || value != strings.ToLower(value) {
		return nil, errors.New("SHA-256 pin must be 64 lowercase hexadecimal characters")
	}
	decoded, err := hex.DecodeString(value)
	if err != nil {
		return nil, errors.New("SHA-256 pin must be 64 lowercase hexadecimal characters")
	}
	return decoded, nil
}
