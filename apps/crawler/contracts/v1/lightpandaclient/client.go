// Package lightpandaclient implements the existing pinned runtime-v1 service conversation.
// Queue selection and persistence remain with its caller.
package lightpandaclient

import (
	"bytes"
	"context"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"encoding/hex"
	"encoding/pem"
	"errors"
	"fmt"
	"io"
	"net"
	"os"
	"syscall"
	"time"

	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/framing"
	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/lightpandaadapter"
	"google.golang.org/protobuf/proto"
)

const (
	rendererALPN     = "jobseek-lightpanda-b0/1"
	runtimeContract  = "crawler.runtime/v1"
	helloFrameLimit  = uint64(512)
	inputFrameLimit  = uint64(128*1024 + 3)
	resultFrameLimit = uint64(2 * 1024 * 1024)
)

// ErrReservationUnavailable identifies a peer close during the reservation handshake.
// The C4 service closes excess connections before TLS; callers may wait within
// their existing context. Dial, identity, hello and execution errors stay distinct.
var ErrReservationUnavailable = errors.New("renderer reservation unavailable")

var canonicalHello = []byte(`{"protocol":"jobseek.lightpanda.service/v1","runtime_contract":"crawler.runtime/v1","mode":"b0","capacity":4,"memory_max_bytes":1073741824,"memory_swap_max_bytes":0}`)

type Config struct {
	RendererAddress, RendererServerName          string
	CAPath, ClientCertificatePath, ClientKeyPath string
	CAPin, ServerLeafPin, ServerSPKIPin          string
}

type Client struct {
	address   string
	tlsConfig *tls.Config
	ca        *x509.Certificate
	leafPin   string
	spkiPin   string
}

type Reservation struct {
	connection *tls.Conn
	used       bool
}

func New(c Config) (*Client, error) {
	caPEM, err := os.ReadFile(c.CAPath)
	if err != nil || len(caPEM) == 0 || len(caPEM) > 128*1024 {
		return nil, errors.New("invalid renderer CA file")
	}
	block, rest := pem.Decode(caPEM)
	if block == nil || block.Type != "CERTIFICATE" || len(rest) != 0 {
		return nil, errors.New("renderer CA must contain one certificate")
	}
	ca, err := x509.ParseCertificate(block.Bytes)
	if err != nil || !ca.IsCA {
		return nil, errors.New("renderer CA is not a CA certificate")
	}
	caDigest := sha256.Sum256(ca.Raw)
	if hex.EncodeToString(caDigest[:]) != c.CAPin {
		return nil, errors.New("renderer CA pin mismatch")
	}
	roots := x509.NewCertPool()
	roots.AddCert(ca)
	identity, err := tls.LoadX509KeyPair(c.ClientCertificatePath, c.ClientKeyPath)
	if err != nil || len(identity.Certificate) != 1 {
		return nil, errors.New("invalid renderer client identity")
	}
	return &Client{address: c.RendererAddress, ca: ca, leafPin: c.ServerLeafPin, spkiPin: c.ServerSPKIPin, tlsConfig: &tls.Config{
		MinVersion: tls.VersionTLS13, MaxVersion: tls.VersionTLS13, RootCAs: roots,
		Certificates: []tls.Certificate{identity}, ServerName: c.RendererServerName, NextProtos: []string{rendererALPN},
		SessionTicketsDisabled: true,
	}}, nil
}

func (r *Client) Reserve(ctx context.Context) (*Reservation, error) {
	dialer := &net.Dialer{Timeout: 10 * time.Second}
	raw, err := dialer.DialContext(ctx, "tcp", r.address)
	if err != nil {
		// A capacity close can race with completion of TCP connect, before
		// TLS starts. Preserve the same bounded retry signal at either seam.
		// Refused connections, DNS failures and cancellation remain distinct.
		if errors.Is(err, syscall.ECONNRESET) || errors.Is(err, syscall.EPIPE) {
			return nil, fmt.Errorf("renderer connect: %w", errors.Join(ErrReservationUnavailable, err))
		}
		return nil, fmt.Errorf("renderer connect: %w", err)
	}
	connection := tls.Client(raw, r.tlsConfig.Clone())
	handshake, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	if err := connection.HandshakeContext(handshake); err != nil {
		_ = raw.Close()
		if errors.Is(err, io.EOF) || errors.Is(err, syscall.ECONNRESET) || errors.Is(err, syscall.EPIPE) {
			return nil, fmt.Errorf("renderer TLS handshake: %w", errors.Join(ErrReservationUnavailable, err))
		}
		return nil, fmt.Errorf("renderer TLS handshake: %w", err)
	}
	state := connection.ConnectionState()
	if !ExactConnection(state, r.ca, r.leafPin, r.spkiPin) {
		_ = connection.Close()
		return nil, errors.New("renderer negotiated invalid identity")
	}
	if err := connection.SetReadDeadline(time.Now().Add(10 * time.Second)); err != nil {
		_ = connection.Close()
		return nil, err
	}
	hello, err := framing.ReadRecord(connection, helloFrameLimit)
	if err != nil || string(hello) != string(canonicalHello) {
		_ = connection.Close()
		return nil, errors.New("renderer hello mismatch")
	}
	_ = connection.SetDeadline(time.Time{})
	return &Reservation{connection: connection}, nil
}

func (r *Reservation) Close() {
	if r != nil && r.connection != nil {
		_ = r.connection.Close()
	}
}

func (r *Reservation) Execute(ctx context.Context, request *runtimev1.BrowserExecutionInput) ([]byte, error) {
	if r == nil || r.connection == nil || r.used {
		return nil, errors.New("renderer reservation is unavailable")
	}
	r.used = true
	stopInterrupt := make(chan struct{})
	go func() {
		select {
		case <-ctx.Done():
			_ = r.connection.Close()
		case <-stopInterrupt:
		}
	}()
	defer close(stopInterrupt)
	if request == nil || request.Plan == nil || request.Plan.Navigation == nil {
		return nil, errors.New("invalid render request")
	}
	payload, err := proto.MarshalOptions{Deterministic: true}.Marshal(request)
	if err != nil {
		return nil, err
	}
	record, err := framing.EncodeRecord(payload, inputFrameLimit)
	if err != nil {
		return nil, err
	}
	record = append(record, 0)
	deadline := time.Now().Add(lightpandaadapter.NavigationExecutionBudget(request.Plan.Navigation) + 15*time.Second)
	if contextDeadline, ok := ctx.Deadline(); ok && contextDeadline.Before(deadline) {
		deadline = contextDeadline
	}
	if err := r.connection.SetDeadline(deadline); err != nil {
		return nil, err
	}
	if err := WriteAll(r.connection, record); err != nil {
		return nil, fmt.Errorf("write renderer request: %w", err)
	}
	resultPayload, err := framing.ReadRecord(r.connection, resultFrameLimit)
	if err != nil {
		return nil, fmt.Errorf("read renderer result: %w", err)
	}
	one := []byte{0}
	count, trailingErr := r.connection.Read(one)
	if count != 0 || !errors.Is(trailingErr, io.EOF) {
		return nil, errors.New("renderer sent trailing bytes")
	}
	result := &runtimev1.BrowserResult{}
	if err := proto.Unmarshal(resultPayload, result); err != nil || len(result.ProtoReflect().GetUnknown()) != 0 ||
		result.ContractVersion != runtimeContract || result.Backend != runtimev1.BrowserBackend_BROWSER_BACKEND_LIGHTPANDA || result.Outcome == nil {
		return nil, errors.New("renderer result violates runtime-v1")
	}
	canonical, err := proto.MarshalOptions{Deterministic: true}.Marshal(result)
	if err != nil || string(canonical) != string(resultPayload) {
		return nil, errors.New("renderer result is not canonical")
	}
	return resultPayload, nil
}

func ExactConnection(state tls.ConnectionState, ca *x509.Certificate, leafPin, spkiPin string) bool {
	if ca == nil || state.Version != tls.VersionTLS13 || state.NegotiatedProtocol != rendererALPN ||
		(len(state.PeerCertificates) != 1 && len(state.PeerCertificates) != 2) ||
		len(state.VerifiedChains) != 1 || len(state.VerifiedChains[0]) != 2 {
		return false
	}
	leaf := state.PeerCertificates[0]
	if len(state.PeerCertificates) == 2 && !bytes.Equal(state.PeerCertificates[1].Raw, ca.Raw) {
		return false
	}
	chain := state.VerifiedChains[0]
	if !bytes.Equal(chain[0].Raw, leaf.Raw) || !bytes.Equal(chain[1].Raw, ca.Raw) {
		return false
	}
	leafDigest, spkiDigest := sha256.Sum256(leaf.Raw), sha256.Sum256(leaf.RawSubjectPublicKeyInfo)
	return hex.EncodeToString(leafDigest[:]) == leafPin && hex.EncodeToString(spkiDigest[:]) == spkiPin
}

func WriteAll(output io.Writer, payload []byte) error {
	for len(payload) != 0 {
		written, err := output.Write(payload)
		if err != nil {
			return err
		}
		if written <= 0 || written > len(payload) {
			return io.ErrShortWrite
		}
		payload = payload[written:]
	}
	return nil
}
