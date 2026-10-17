package main

import (
	"context"
	"crypto"
	"crypto/rand"
	"crypto/rsa"
	"crypto/sha256"
	"crypto/x509"
	"encoding/base64"
	"encoding/json"
	"encoding/pem"
	"errors"
	"flag"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"time"
)

var hostAppID = regexp.MustCompile(`^[1-9][0-9]{0,19}$`)

func hostCredential(path string, limit int64) ([]byte, error) {
	info, e := os.Lstat(path)
	if e != nil || !info.Mode().IsRegular() || info.Size() <= 0 || info.Size() > limit {
		return nil, errors.New("credential is not a bounded regular file")
	}
	raw, e := boundedInventoryFile(path, limit)
	if e != nil || strings.TrimSpace(string(raw)) == "" {
		return nil, errors.New("credential unreadable or empty")
	}
	return raw, nil
}
func hostAppJWT(appID string, keyPEM []byte, now int64) (string, error) {
	if !hostAppID.MatchString(appID) {
		return "", errors.New("invalid GitHub App ID")
	}
	block, _ := pem.Decode(keyPEM)
	if block == nil {
		return "", errors.New("invalid GitHub App PEM")
	}
	var key *rsa.PrivateKey
	if parsed, e := x509.ParsePKCS8PrivateKey(block.Bytes); e == nil {
		key, _ = parsed.(*rsa.PrivateKey)
	} else {
		key, _ = x509.ParsePKCS1PrivateKey(block.Bytes)
	}
	if key == nil {
		return "", errors.New("GitHub App requires an RSA private key")
	}
	header := base64.RawURLEncoding.EncodeToString([]byte(`{"alg": "RS256", "typ": "JWT"}`))
	payload, _ := json.Marshal(map[string]any{"iat": now - 60, "exp": now + 540, "iss": appID})
	body := header + "." + base64.RawURLEncoding.EncodeToString(payload)
	hash := sha256.Sum256([]byte(body))
	signature, e := rsa.SignPKCS1v15(rand.Reader, key, crypto.SHA256, hash[:])
	if e != nil {
		return "", errors.New("GitHub App signing failed")
	}
	return body + "." + base64.RawURLEncoding.EncodeToString(signature), nil
}
func mintHostToken(ctx context.Context, client inventoryHTTPClient, appID, installationID string, key []byte, now int64) (string, error) {
	if !hostAppID.MatchString(installationID) {
		return "", errors.New("invalid GitHub App installation ID")
	}
	jwt, e := hostAppJWT(appID, key, now)
	if e != nil {
		return "", e
	}
	req, e := http.NewRequestWithContext(ctx, http.MethodPost, "https://api.github.com/app/installations/"+installationID+"/access_tokens", strings.NewReader("{}"))
	if e != nil {
		return "", errors.New("GitHub App request unavailable")
	}
	req.Header.Set("Accept", "application/vnd.github+json")
	req.Header.Set("Authorization", "Bearer "+jwt)
	req.Header.Set("X-GitHub-Api-Version", "2022-11-28")
	req.Header.Set("User-Agent", "jobseek-ats-inventory-deployer/1")
	response, e := client.Do(req)
	if e != nil {
		return "", errors.New("GitHub App token request failed")
	}
	defer response.Body.Close()
	if response.StatusCode != 200 && response.StatusCode != 201 {
		return "", errors.New("GitHub App token request rejected")
	}
	raw, e := io.ReadAll(io.LimitReader(response.Body, 65537))
	if e != nil || len(raw) > 65536 {
		return "", errors.New("GitHub App response exceeds limit")
	}
	var payload map[string]json.RawMessage
	var token string
	if json.Unmarshal(raw, &payload) != nil || json.Unmarshal(payload["token"], &token) != nil || token == "" || len(token) > 4096 || strings.TrimSpace(token) != token || strings.ContainsAny(token, "\r\n") {
		return "", errors.New("invalid GitHub App token response")
	}
	return token, nil
}
func runHostToken(ctx context.Context, args []string) error {
	f := flag.NewFlagSet("host-token", flag.ContinueOnError)
	f.SetOutput(io.Discard)
	root := f.String("credentials-dir", "", "")
	output := f.String("output", "", "")
	if e := f.Parse(args); e != nil {
		return e
	}
	if *root == "" || *output == "" || f.NArg() != 0 {
		return errors.New("invalid token arguments")
	}
	app, e := hostCredential(filepath.Join(*root, "github-app-id"), 64)
	if e != nil {
		return e
	}
	installation, e := hostCredential(filepath.Join(*root, "github-app-installation-id"), 64)
	if e != nil {
		return e
	}
	key, e := hostCredential(filepath.Join(*root, "github-app-private-key"), 65536)
	if e != nil {
		return e
	}
	requestContext, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
	client := &http.Client{Timeout: 30 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	token, e := mintHostToken(requestContext, client, strings.TrimSpace(string(app)), strings.TrimSpace(string(installation)), key, time.Now().Unix())
	if e != nil {
		return e
	}
	return atomicHostBytes(*output, []byte(token+"\n"), 0600, 0700)
}
