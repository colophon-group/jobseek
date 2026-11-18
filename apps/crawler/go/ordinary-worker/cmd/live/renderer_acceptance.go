package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"time"

	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	lp "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/lightpandaclient"
	executor "github.com/colophon-group/jobseek/apps/crawler/go/lightpanda-b0-executor"
	"google.golang.org/protobuf/proto"
)

const rendererAcceptanceTarget = "https://example.com/"
const rendererAcceptanceCredentials = "/run/lightpanda"

var errRendererAcceptance = errors.New("renderer acceptance rejected")
var acceptancePinPattern = regexp.MustCompile(`^[0-9a-f]{64}$`)
var acceptanceCredentialFiles = [...]string{"ca.pem", "ca.sha256", "client-key.pem", "client.pem", "server-leaf.sha256", "server-spki.sha256"}

type rendererAcceptanceReport struct {
	Status                 string `json:"status"`
	Target                 string `json:"target"`
	HTTPStatus             uint32 `json:"http_status"`
	CredentialBundleSHA256 string `json:"credential_bundle_sha256"`
	CrawlerSourceRevision  string `json:"crawler_source_revision"`
}

// This command runs before daemon configuration, database, queue or model setup.
// Neither its target, renderer identity nor credential paths accept environment
// or CLI overrides. The host workflow supplies only the existing scoped bundle.
func rendererAcceptance(ctx context.Context, revision string) (*rendererAcceptanceReport, error) {
	config, digest, err := acceptanceCredentials(rendererAcceptanceCredentials)
	if err != nil {
		return nil, errRendererAcceptance
	}
	return executeRendererAcceptance(ctx, config, digest, revision)
}

func acceptanceCredentials(root string) (lp.Config, string, error) {
	digest := sha256.New()
	pins := map[string]string{}
	// The installed credential generation is traversal-only to UID 10001.
	// Read the fixed files directly, never enumerate that directory.
	for _, name := range acceptanceCredentialFiles {
		path := filepath.Join(root, name)
		info, err := os.Lstat(path)
		if err != nil || !info.Mode().IsRegular() || info.Size() < 1 || info.Size() > 128*1024 {
			return lp.Config{}, "", errRendererAcceptance
		}
		file, err := os.Open(path)
		if err != nil {
			return lp.Config{}, "", errRendererAcceptance
		}
		opened, statErr := file.Stat()
		body, readErr := io.ReadAll(io.LimitReader(file, 128*1024+1))
		closeErr := file.Close()
		if statErr != nil || !os.SameFile(info, opened) || readErr != nil || closeErr != nil || len(body) != int(info.Size()) {
			return lp.Config{}, "", errRendererAcceptance
		}
		digest.Write([]byte(name + "\x00"))
		digest.Write(body)
		digest.Write([]byte{0})
		if strings.HasSuffix(name, ".sha256") {
			if len(body) != 65 || body[64] != '\n' || !acceptancePinPattern.Match(body[:64]) {
				return lp.Config{}, "", errRendererAcceptance
			}
			pins[name] = string(body[:64])
		}
	}
	return lp.Config{RendererAddress: "10.0.0.5:9443", RendererServerName: "10.0.0.5", CAPath: filepath.Join(root, "ca.pem"), ClientCertificatePath: filepath.Join(root, "client.pem"), ClientKeyPath: filepath.Join(root, "client-key.pem"), CAPin: pins["ca.sha256"], ServerLeafPin: pins["server-leaf.sha256"], ServerSPKIPin: pins["server-spki.sha256"]}, hex.EncodeToString(digest.Sum(nil)), nil
}

func executeRendererAcceptance(parent context.Context, config lp.Config, digest, revision string) (*rendererAcceptanceReport, error) {
	// One reservation and one 15-second navigation, with no transport retries,
	// fallback, proxy, actions or arbitrary target support. The outer deadline
	// also bounds connection/hello and shutdown inside the host's 90-second gate.
	ctx, cancel := context.WithTimeout(parent, 60*time.Second)
	defer cancel()
	client, err := lp.New(config)
	if err != nil {
		return nil, errRendererAcceptance
	}
	input, err := lp.NavigationInput(lp.Navigation{URL: rendererAcceptanceTarget, RoutingRevision: "host-acceptance-v1", OriginRequestID: "host-acceptance-public", Wait: "load", TimeoutMS: 15000})
	if err != nil {
		return nil, errRendererAcceptance
	}
	reservation, err := client.Reserve(ctx)
	if err != nil {
		return nil, errRendererAcceptance
	}
	defer reservation.Close()
	body, err := reservation.Execute(ctx, input)
	if err != nil {
		return nil, errRendererAcceptance
	}
	result := &runtimev1.BrowserResult{}
	if proto.Unmarshal(body, result) != nil {
		return nil, errRendererAcceptance
	}
	success := result.GetSuccess()
	if success == nil || success.GetStatus() != 200 || success.FinalUrl != rendererAcceptanceTarget || success.Html == nil || !success.Html.Complete || success.Html.TotalSizeBytes == 0 {
		return nil, errRendererAcceptance
	}
	if html, err := executor.RenderedHTML(result, rendererAcceptanceTarget); err != nil || len(html) == 0 {
		return nil, errRendererAcceptance
	}
	return &rendererAcceptanceReport{Status: "accepted", Target: rendererAcceptanceTarget, HTTPStatus: 200, CredentialBundleSHA256: digest, CrawlerSourceRevision: revision}, nil
}
