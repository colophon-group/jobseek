package main

import (
	"bytes"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"encoding/hex"
	"testing"
)

type boundedWriter struct {
	limit int
	data  bytes.Buffer
}

func (w *boundedWriter) Write(payload []byte) (int, error) {
	if len(payload) > w.limit {
		payload = payload[:w.limit]
	}
	return w.data.Write(payload)
}

func TestWriteAllHandlesShortRendererWrites(t *testing.T) {
	writer := &boundedWriter{limit: 3}
	payload := []byte("complete-render-request")
	if err := writeAll(writer, payload); err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(writer.data.Bytes(), payload) {
		t.Fatal("short writes truncated the renderer request")
	}
}

func TestRendererAcceptsLeafOnlyOrLeafAndPinnedRootPresentation(t *testing.T) {
	leaf := &x509.Certificate{Raw: []byte("leaf"), RawSubjectPublicKeyInfo: []byte("spki")}
	root := &x509.Certificate{Raw: []byte("pinned-root")}
	leafDigest, spkiDigest := sha256.Sum256(leaf.Raw), sha256.Sum256(leaf.RawSubjectPublicKeyInfo)
	state := tls.ConnectionState{
		Version: tls.VersionTLS13, NegotiatedProtocol: rendererALPN,
		PeerCertificates: []*x509.Certificate{leaf},
		VerifiedChains:   [][]*x509.Certificate{{leaf, root}},
	}
	leafPin, spkiPin := hex.EncodeToString(leafDigest[:]), hex.EncodeToString(spkiDigest[:])
	if !exactRendererConnection(state, root, leafPin, spkiPin) {
		t.Fatal("leaf-only presentation was rejected")
	}
	state.PeerCertificates = []*x509.Certificate{leaf, root}
	if !exactRendererConnection(state, root, leafPin, spkiPin) {
		t.Fatal("Python/OpenSSL leaf plus pinned-root presentation was rejected")
	}
	state.PeerCertificates = []*x509.Certificate{leaf, {Raw: []byte("wrong-root")}}
	if exactRendererConnection(state, root, leafPin, spkiPin) {
		t.Fatal("untrusted presented root was accepted")
	}
	state.PeerCertificates = []*x509.Certificate{leaf, root, root}
	if exactRendererConnection(state, root, leafPin, spkiPin) {
		t.Fatal("surplus presented chain was accepted")
	}
}

func TestBrowserRequestFingerprintMatchesPythonCanonicalJSON(t *testing.T) {
	task := validQueueTask(t)
	task.Envelope.SourceURL = "https://jobs.example.com/posting?a=1&b=<x>"
	request, err := browserInput(task)
	if err != nil {
		t.Fatal(err)
	}
	const expected = "8c9e7d1c801cb50026bbcfe31a4b6e533d0f6828367b3dfc5e036f7a563dd1c0"
	if got := request.Plan.OriginOperations[0].RequestFingerprint; got != expected {
		t.Fatalf("request fingerprint diverged from Python canonical JSON: got %s", got)
	}
}
