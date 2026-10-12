// Package claimantbundle prepares the immutable, offline-validated claimant PKI.
package claimantbundle

import (
	"bytes"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/sha256"
	"crypto/x509"
	"encoding/asn1"
	"encoding/hex"
	"encoding/pem"
	"errors"
	"net"
	"time"
)

var (
	constraintsOID = asn1.ObjectIdentifier{2, 5, 29, 19}
	usageOID       = asn1.ObjectIdentifier{2, 5, 29, 15}
	extendedOID    = asn1.ObjectIdentifier{2, 5, 29, 37}
	sanOID         = asn1.ObjectIdentifier{2, 5, 29, 17}
)

func reject(reason string) error { return errors.New("claimant bundle rejected: " + reason) }
func digest(body []byte) string  { sum := sha256.Sum256(body); return hex.EncodeToString(sum[:]) }

func certificate(body []byte) (*x509.Certificate, error) {
	block, rest := pem.Decode(body)
	if block == nil || block.Type != "CERTIFICATE" || len(block.Headers) != 0 || len(bytes.TrimSpace(rest)) != 0 || !bytes.Equal(bytes.TrimSpace(body), bytes.TrimSpace(pem.EncodeToMemory(block))) {
		return nil, reject("noncanonical certificate")
	}
	cert, err := x509.ParseCertificate(block.Bytes)
	if err != nil {
		return nil, reject("invalid certificate")
	}
	key, ok := cert.PublicKey.(*ecdsa.PublicKey)
	if !ok || key.Curve != elliptic.P256() || cert.SignatureAlgorithm != x509.ECDSAWithSHA256 {
		return nil, reject("certificate signature profile")
	}
	return cert, nil
}

func extension(cert *x509.Certificate, oid asn1.ObjectIdentifier, critical bool) ([]byte, error) {
	for _, ext := range cert.Extensions {
		if ext.Id.Equal(oid) {
			if ext.Critical != critical {
				return nil, reject("extension criticality")
			}
			return ext.Value, nil
		}
	}
	return nil, reject("required certificate extension absent")
}

func validity(cert *x509.Certificate, now time.Time, minimum, maximum int) error {
	lifetime := cert.NotAfter.Sub(cert.NotBefore)
	if lifetime < time.Duration(minimum)*24*time.Hour || lifetime > time.Duration(maximum)*24*time.Hour || cert.NotBefore.After(now) || !cert.NotAfter.After(now.Add(30*24*time.Hour)) {
		return reject("certificate validity")
	}
	return nil
}

func validateLeaf(cert, ca *x509.Certificate, usage x509.ExtKeyUsage, sanTag int, identity []byte, now time.Time) error {
	if !bytes.Equal(cert.RawIssuer, ca.RawSubject) || cert.CheckSignatureFrom(ca) != nil {
		return reject("leaf issuer or signature")
	}
	constraints, err := extension(cert, constraintsOID, true)
	if err != nil || !bytes.Equal(constraints, []byte{0x30, 0x00}) {
		return reject("leaf constraints")
	}
	if _, err = extension(cert, usageOID, true); err != nil || cert.KeyUsage != x509.KeyUsageDigitalSignature {
		return reject("leaf key usage")
	}
	if _, err = extension(cert, extendedOID, false); err != nil || len(cert.ExtKeyUsage) != 1 || cert.ExtKeyUsage[0] != usage || len(cert.UnknownExtKeyUsage) != 0 {
		return reject("leaf extended usage")
	}
	san, err := extension(cert, sanOID, false)
	if err != nil {
		return err
	}
	var sequence asn1.RawValue
	rest, err := asn1.Unmarshal(san, &sequence)
	if err != nil || len(rest) != 0 || sequence.Class != 0 || sequence.Tag != 16 || !sequence.IsCompound {
		return reject("leaf SAN")
	}
	var name asn1.RawValue
	rest, err = asn1.Unmarshal(sequence.Bytes, &name)
	if err != nil || len(rest) != 0 || name.Class != 2 || name.Tag != sanTag || name.IsCompound || !bytes.Equal(name.Bytes, identity) {
		return reject("leaf identity")
	}
	return validity(cert, now, 170, 190)
}

func validateMaterial(payloads map[string][]byte, host string, now time.Time) error {
	address := net.ParseIP(host)
	if address == nil {
		return reject("server host must be a literal IP")
	}
	ca, err := certificate(payloads["ca.pem"])
	if err != nil {
		return err
	}
	client, err := certificate(payloads["client.pem"])
	if err != nil {
		return err
	}
	server, err := certificate(payloads["server.pem"])
	if err != nil {
		return err
	}
	if !bytes.Equal(ca.RawSubject, ca.RawIssuer) || ca.CheckSignatureFrom(ca) != nil {
		return reject("CA self-signature")
	}
	if _, err = extension(ca, constraintsOID, true); err != nil || !ca.BasicConstraintsValid || !ca.IsCA || !ca.MaxPathLenZero || ca.MaxPathLen != 0 {
		return reject("CA constraints")
	}
	if _, err = extension(ca, usageOID, true); err != nil || ca.KeyUsage != x509.KeyUsageCertSign|x509.KeyUsageCRLSign {
		return reject("CA key usage")
	}
	for _, ext := range ca.Extensions {
		if ext.Id.Equal(sanOID) || ext.Id.Equal(extendedOID) {
			return reject("CA leaf extensions")
		}
	}
	if err = validity(ca, now, 1000, 1120); err != nil {
		return err
	}
	if err = validateLeaf(client, ca, x509.ExtKeyUsageClientAuth, 6, []byte("spiffe://jobseek/crawler/lightpanda-b0"), now); err != nil {
		return err
	}
	if ipv4 := address.To4(); ipv4 != nil {
		address = ipv4
	} else {
		address = address.To16()
	}
	if err = validateLeaf(server, ca, x509.ExtKeyUsageServerAuth, 7, address, now); err != nil {
		return err
	}
	block, rest := pem.Decode(payloads["client-key.pem"])
	if block == nil || block.Type != "PRIVATE KEY" || len(block.Headers) != 0 || len(bytes.TrimSpace(rest)) != 0 {
		return reject("private key encoding")
	}
	key, err := x509.ParsePKCS8PrivateKey(block.Bytes)
	if err != nil {
		return reject("private key parse")
	}
	ec, ok := key.(*ecdsa.PrivateKey)
	if !ok || ec.Curve != elliptic.P256() {
		return reject("private key profile")
	}
	canonical, err := x509.MarshalPKCS8PrivateKey(ec)
	if err != nil || !bytes.Equal(bytes.TrimSpace(payloads["client-key.pem"]), bytes.TrimSpace(pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: canonical}))) {
		return reject("noncanonical private key")
	}
	spki, err := x509.MarshalPKIXPublicKey(&ec.PublicKey)
	if err != nil || !bytes.Equal(spki, client.RawSubjectPublicKeyInfo) {
		return reject("private key mismatch")
	}
	for name, value := range map[string]string{"ca.sha256": digest(ca.Raw), "server-leaf.sha256": digest(server.Raw), "server-spki.sha256": digest(server.RawSubjectPublicKeyInfo)} {
		if !bytes.Equal(payloads[name], []byte(value+"\n")) {
			return reject("certificate pin mismatch")
		}
	}
	return nil
}
