package claimantbundle

import (
	"archive/tar"
	"bytes"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/pem"
	"errors"
	"math/big"
	"net"
	"net/url"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"testing"
	"time"
)

const testRevision = "1234567890123456789012345678901234567890"

func material(t *testing.T, mutate func(*x509.Certificate, *x509.Certificate, *x509.Certificate)) map[string][]byte {
	t.Helper()
	now := time.Now().UTC().Truncate(time.Second)
	ca := &x509.Certificate{SerialNumber: big.NewInt(1), Subject: pkix.Name{CommonName: "synthetic CA"}, NotBefore: now.Add(-time.Hour), NotAfter: now.Add(1050*24*time.Hour - time.Hour), BasicConstraintsValid: true, IsCA: true, MaxPathLenZero: true, KeyUsage: x509.KeyUsageCertSign | x509.KeyUsageCRLSign}
	identity, _ := url.Parse("spiffe://jobseek/crawler/lightpanda-b0")
	client := &x509.Certificate{SerialNumber: big.NewInt(2), Subject: pkix.Name{CommonName: "synthetic client"}, NotBefore: now.Add(-time.Hour), NotAfter: now.Add(180*24*time.Hour - time.Hour), BasicConstraintsValid: true, KeyUsage: x509.KeyUsageDigitalSignature, ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageClientAuth}, URIs: []*url.URL{identity}}
	server := &x509.Certificate{SerialNumber: big.NewInt(3), Subject: pkix.Name{CommonName: "synthetic server"}, NotBefore: client.NotBefore, NotAfter: client.NotAfter, BasicConstraintsValid: true, KeyUsage: x509.KeyUsageDigitalSignature, ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth}, IPAddresses: []net.IP{net.ParseIP("10.0.0.5")}}
	if mutate != nil {
		mutate(ca, client, server)
	}
	keys := make([]*ecdsa.PrivateKey, 3)
	for i := range keys {
		var err error
		keys[i], err = ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
		if err != nil {
			t.Fatal(err)
		}
	}
	caDER, err := x509.CreateCertificate(rand.Reader, ca, ca, &keys[0].PublicKey, keys[0])
	if err != nil {
		t.Fatal(err)
	}
	parsedCA, err := x509.ParseCertificate(caDER)
	if err != nil {
		t.Fatal(err)
	}
	clientDER, err := x509.CreateCertificate(rand.Reader, client, parsedCA, &keys[1].PublicKey, keys[0])
	if err != nil {
		t.Fatal(err)
	}
	serverDER, err := x509.CreateCertificate(rand.Reader, server, parsedCA, &keys[2].PublicKey, keys[0])
	if err != nil {
		t.Fatal(err)
	}
	parsedServer, err := x509.ParseCertificate(serverDER)
	if err != nil {
		t.Fatal(err)
	}
	keyDER, err := x509.MarshalPKCS8PrivateKey(keys[1])
	if err != nil {
		t.Fatal(err)
	}
	return map[string][]byte{"ca.pem": pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: caDER}), "client.pem": pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: clientDER}), "server.pem": pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: serverDER}), "client-key.pem": pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: keyDER}), "ca.sha256": []byte(digest(caDER) + "\n"), "server-leaf.sha256": []byte(digest(serverDER) + "\n"), "server-spki.sha256": []byte(digest(parsedServer.RawSubjectPublicKeyInfo) + "\n")}
}

func transport(t *testing.T, payloads map[string][]byte, mutate func(*tar.Header)) (string, string) {
	t.Helper()
	var body bytes.Buffer
	w := tar.NewWriter(&body)
	names := []string{}
	for name := range payloads {
		names = append(names, name)
	}
	sort.Strings(names)
	for _, name := range names {
		mode := fileModes[name]
		if name == "server.pem" {
			mode = 0444
		}
		h := &tar.Header{Name: name, Size: int64(len(payloads[name])), Mode: int64(mode), Typeflag: tar.TypeReg, Format: tar.FormatUSTAR}
		if mutate != nil {
			mutate(h)
		}
		if err := w.WriteHeader(h); err != nil {
			t.Fatal(err)
		}
		if h.Typeflag == tar.TypeReg {
			if _, err := w.Write(payloads[name]); err != nil {
				t.Fatal(err)
			}
		}
	}
	if err := w.Close(); err != nil {
		t.Fatal(err)
	}
	file := filepath.Join(t.TempDir(), "transport.tar")
	if err := os.WriteFile(file, body.Bytes(), 0600); err != nil {
		t.Fatal(err)
	}
	return file, digest(body.Bytes())
}

func TestPreparePublishesOriginalSixFilesAndRetriesExactGeneration(t *testing.T) {
	payloads := material(t, nil)
	archive, sha := transport(t, payloads, nil)
	root := filepath.Join(t.TempDir(), "credentials")
	path, err := Prepare(archive, sha, testRevision, root, "10.0.0.5")
	if err != nil {
		t.Fatal(err)
	}
	if filepath.Base(path) != "sha-"+testRevision+"-"+sha {
		t.Fatal("generation identity changed")
	}
	entries, err := os.ReadDir(path)
	if err != nil || len(entries) != 6 {
		t.Fatal("generation set", err)
	}
	for name, mode := range fileModes {
		info, err := os.Lstat(filepath.Join(path, name))
		if err != nil || info.Mode().Perm() != mode {
			t.Fatal("file mode", name, err)
		}
		body, err := os.ReadFile(filepath.Join(path, name))
		if err != nil || !bytes.Equal(body, payloads[name]) {
			t.Fatal("file bytes", name, err)
		}
	}
	if info, _ := os.Stat(root); info.Mode().Perm() != 0700 {
		t.Fatal("root mode")
	}
	if info, _ := os.Stat(path); info.Mode().Perm() != 0711 {
		t.Fatal("generation mode")
	}
	again, err := Prepare(archive, sha, testRevision, root, "10.0.0.5")
	if err != nil || again != path {
		t.Fatal("idempotent retry", err)
	}
}

func TestPrepareRejectsOriginalCryptographicFailureClasses(t *testing.T) {
	for name, mutate := range map[string]func(*x509.Certificate, *x509.Certificate, *x509.Certificate){
		"CA lifetime":          func(c, a, b *x509.Certificate) { c.NotAfter = c.NotBefore.Add(900 * 24 * time.Hour) },
		"CA path length":       func(c, a, b *x509.Certificate) { c.MaxPathLen = 1; c.MaxPathLenZero = false },
		"CA usage":             func(c, a, b *x509.Certificate) { c.KeyUsage |= x509.KeyUsageDigitalSignature },
		"CA leaf extensions":   func(c, a, b *x509.Certificate) { c.ExtKeyUsage = []x509.ExtKeyUsage{x509.ExtKeyUsageClientAuth} },
		"client identity":      func(c, a, b *x509.Certificate) { u, _ := url.Parse("spiffe://foreign/client"); a.URIs = []*url.URL{u} },
		"client multiple SANs": func(c, a, b *x509.Certificate) { a.DNSNames = []string{"extra.invalid"} },
		"client usage":         func(c, a, b *x509.Certificate) { a.ExtKeyUsage = []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth} },
		"client lifetime":      func(c, a, b *x509.Certificate) { a.NotAfter = a.NotBefore.Add(160 * 24 * time.Hour) },
		"client not valid yet": func(c, a, b *x509.Certificate) {
			a.NotBefore = time.Now().Add(time.Hour)
			a.NotAfter = a.NotBefore.Add(180 * 24 * time.Hour)
		},
		"server identity":          func(c, a, b *x509.Certificate) { b.IPAddresses = []net.IP{net.ParseIP("10.0.0.6")} },
		"server extra usages":      func(c, a, b *x509.Certificate) { b.ExtKeyUsage = append(b.ExtKeyUsage, x509.ExtKeyUsageClientAuth) },
		"leaf signature algorithm": func(c, a, b *x509.Certificate) { a.SignatureAlgorithm = x509.ECDSAWithSHA384 },
	} {
		t.Run(name, func(t *testing.T) {
			archive, sha := transport(t, material(t, mutate), nil)
			root := filepath.Join(t.TempDir(), "credentials")
			if _, err := Prepare(archive, sha, testRevision, root, "10.0.0.5"); err == nil {
				t.Fatal("invalid material admitted")
			}
			if _, err := os.Lstat(root); !os.IsNotExist(err) {
				t.Fatal("failed cryptography created a root")
			}
		})
	}
	for _, name := range []string{"pin mismatch", "bad signature", "key mismatch", "certificate trailing text", "private key trailing text"} {
		t.Run(name, func(t *testing.T) {
			p := material(t, nil)
			switch name {
			case "pin mismatch":
				p["server-leaf.sha256"] = []byte(strings.Repeat("a", 64) + "\n")
			case "bad signature":
				block, _ := pem.Decode(p["client.pem"])
				block.Bytes[len(block.Bytes)-1] ^= 1
				p["client.pem"] = pem.EncodeToMemory(block)
			case "key mismatch":
				other := material(t, nil)
				p["client-key.pem"] = other["client-key.pem"]
			case "certificate trailing text":
				p["client.pem"] = append(p["client.pem"], []byte("extra")...)
			case "private key trailing text":
				p["client-key.pem"] = append(p["client-key.pem"], []byte("extra")...)
			}
			archive, sha := transport(t, p, nil)
			if _, err := Prepare(archive, sha, testRevision, filepath.Join(t.TempDir(), "credentials"), "10.0.0.5"); err == nil {
				t.Fatal("invalid material admitted")
			}
		})
	}
}

func TestPrepareRejectsArchiveAndFilesystemDrift(t *testing.T) {
	for _, name := range []string{"digest", "revision", "archive mode", "archive symlink", "root symlink", "member mode", "member path", "member symlink", "incomplete set", "generation mode", "generation extra member", "generation file mode", "generation bytes", "generation symlink"} {
		t.Run(name, func(t *testing.T) {
			p := material(t, nil)
			var mutate func(*tar.Header)
			if name == "incomplete set" {
				delete(p, "server-spki.sha256")
			}
			if name == "member mode" {
				mutate = func(h *tar.Header) {
					if h.Name == "client-key.pem" {
						h.Mode = 0444
					}
				}
			}
			if name == "member path" {
				mutate = func(h *tar.Header) {
					if h.Name == "client-key.pem" {
						h.Name = "../client-key.pem"
					}
				}
			}
			if name == "member symlink" {
				mutate = func(h *tar.Header) {
					if h.Name == "client-key.pem" {
						h.Typeflag = tar.TypeSymlink
						h.Size = 0
						h.Linkname = "/tmp/foreign"
					}
				}
			}
			archive, sha := transport(t, p, mutate)
			revision := testRevision
			root := filepath.Join(t.TempDir(), "credentials")
			switch name {
			case "digest":
				sha = strings.Repeat("a", 64)
			case "revision":
				revision = "invalid"
			case "archive mode":
				if err := os.Chmod(archive, 0644); err != nil {
					t.Fatal(err)
				}
			case "archive symlink":
				link := archive + ".link"
				if err := os.Symlink(archive, link); err != nil {
					t.Fatal(err)
				}
				archive = link
			case "root symlink":
				if err := os.Symlink(t.TempDir(), root); err != nil {
					t.Fatal(err)
				}
			}
			if strings.HasPrefix(name, "generation ") {
				path, err := Prepare(archive, sha, revision, root, "10.0.0.5")
				if err != nil {
					t.Fatal(err)
				}
				switch name {
				case "generation mode":
					err = os.Chmod(path, 0700)
				case "generation extra member":
					err = os.WriteFile(filepath.Join(path, "foreign"), []byte("x"), 0600)
				case "generation file mode":
					err = os.Chmod(filepath.Join(path, "ca.pem"), 0600)
				case "generation bytes":
					file := filepath.Join(path, "ca.pem")
					if err = os.Chmod(file, 0600); err == nil {
						err = os.WriteFile(file, []byte(strings.Repeat("x", len(p["ca.pem"]))), 0600)
					}
					if err == nil {
						err = os.Chmod(file, 0444)
					}
				case "generation symlink":
					file := filepath.Join(path, "ca.pem")
					if err = os.Remove(file); err == nil {
						err = os.Symlink(archive, file)
					}
				}
				if err != nil {
					t.Fatal(err)
				}
			}
			if _, err := Prepare(archive, sha, revision, root, "10.0.0.5"); err == nil {
				t.Fatal("unsafe archive or filesystem admitted")
			}
		})
	}
}

func TestPrepareFsyncFailureRetainsOriginalPublicationRecovery(t *testing.T) {
	for _, failAt := range []int{1, 2} {
		t.Run(string(rune('0'+failAt)), func(t *testing.T) {
			p := material(t, nil)
			archive, sha := transport(t, p, nil)
			root := filepath.Join(t.TempDir(), "credentials")
			calls := 0
			_, err := prepare(archive, sha, testRevision, root, "10.0.0.5", func(path string) error {
				calls++
				if calls == failAt {
					return errors.New("synthetic fsync failure")
				}
				return syncDirectory(path)
			})
			if err == nil {
				t.Fatal("fsync failure admitted")
			}
			entries, err := os.ReadDir(root)
			if err != nil {
				t.Fatal(err)
			}
			for _, e := range entries {
				if strings.HasPrefix(e.Name(), ".candidate-") {
					t.Fatal("partial candidate survived")
				}
			}
			if failAt == 1 && len(entries) != 0 || failAt == 2 && len(entries) != 1 {
				t.Fatal("publication boundary changed")
			}
			if _, err = Prepare(archive, sha, testRevision, root, "10.0.0.5"); err != nil {
				t.Fatal("exact retry failed", err)
			}
		})
	}
}
