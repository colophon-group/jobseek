package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"os"
	"syscall"

	"github.com/chromedp/cdproto/target"
	"github.com/chromedp/chromedp"
	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/browserproxy"
)

const configuredBrowserProxyFile = "/run/lightpanda/proxies.json"
const configuredBrowserProxyCheckFlag = "--runtime-v1-proxy-settings-check"

var errBrowserProxy = errors.New("configured browser proxy unavailable")
var errBrowserProxyNetworkPolicy = errors.New("configured browser proxy network policy startup failed")

func parseBrowserProxies(raw []byte) (map[string]browserproxy.Endpoint, error) {
	if len(raw) == 0 || len(raw) > 1<<20 {
		return nil, errBrowserProxy
	}
	var entries []string
	if json.Unmarshal(raw, &entries) != nil || entries == nil || len(entries) > 64 {
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
	raw, err := readBrowserProxySettings(path)
	if err != nil || raw == nil {
		return nil, err
	}
	return parseBrowserProxies(raw)
}

func readBrowserProxySettings(path string) ([]byte, error) {
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
	if !ok || metadata.Uid != 10001 || metadata.Gid != 10001 || metadata.Nlink != 1 {
		return nil, errBrowserProxy
	}
	raw, err := io.ReadAll(io.LimitReader(f, (1<<20)+1))
	if err != nil {
		return nil, errBrowserProxy
	}
	return raw, nil
}

func checkBrowserProxySettings(path, expected string) error {
	if len(expected) != 64 {
		return errBrowserProxy
	}
	decoded, err := hex.DecodeString(expected)
	if err != nil || len(decoded) != sha256.Size {
		return errBrowserProxy
	}
	raw, err := readBrowserProxySettings(path)
	if err != nil || raw == nil {
		return errBrowserProxy
	}
	digest := sha256.Sum256(raw)
	if hex.EncodeToString(digest[:]) != expected {
		return errBrowserProxy
	}
	_, err = parseBrowserProxies(raw)
	return err
}

// The child never receives the upstream endpoint or its credentials.
func configuredProxyContext(address string) chromedp.ContextOption {
	return chromedp.WithNewBrowserContext(func(params *target.CreateBrowserContextParams) *target.CreateBrowserContextParams {
		return params.WithProxyServer(address).WithDisposeOnDetach(false)
	})
}
