package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"math/rand"
	"net"
	"net/http"
	"net/url"
	"strings"
	"time"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/aws/signer/v4"
)

type r2Client struct {
	endpoint string
	bucket   string
	creds    aws.Credentials
	signer   *v4.Signer
	http     *http.Client
}

type putError struct {
	status int
	err    error
}

func (e *putError) Error() string { return e.err.Error() }
func (e *putError) Unwrap() error { return e.err }

func newR2Client(endpoint, bucket, accessKey, secretKey string) (*r2Client, error) {
	u, err := url.Parse(endpoint)
	if err != nil || u.Scheme != "https" || u.Host == "" || u.RawQuery != "" || u.Fragment != "" {
		return nil, errors.New("R2_ENDPOINT_URL must be an HTTPS origin or path without query")
	}
	if bucket == "" || strings.ContainsAny(bucket, "/?#") || accessKey == "" || secretKey == "" {
		return nil, errors.New("R2 bucket and credentials are required")
	}
	transport := &http.Transport{
		Proxy:                 http.ProxyFromEnvironment,
		DialContext:           (&net.Dialer{Timeout: 10 * time.Second, KeepAlive: 30 * time.Second}).DialContext,
		MaxIdleConns:          30,
		MaxIdleConnsPerHost:   30,
		IdleConnTimeout:       90 * time.Second,
		TLSHandshakeTimeout:   10 * time.Second,
		ResponseHeaderTimeout: 30 * time.Second,
	}
	return &r2Client{
		endpoint: strings.TrimRight(endpoint, "/"),
		bucket:   bucket,
		creds:    aws.Credentials{AccessKeyID: accessKey, SecretAccessKey: secretKey},
		signer:   v4.NewSigner(),
		http: &http.Client{
			Transport: transport, Timeout: 40 * time.Second,
			// R2's endpoint is fixed. Do not follow a redirect to another host
			// with a signed request or change the Python client's request count.
			CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse },
		},
	}, nil
}

func objectURL(endpoint, bucket string, d description) string {
	// The Python key is job/{posting_id}/{locale}/latest.html, URL-quoted with
	// slashes preserved. Quote individual segments to keep that exact layout.
	return strings.TrimRight(endpoint, "/") + "/" + url.PathEscape(bucket) +
		"/job/" + url.PathEscape(d.PostingID) + "/" + url.PathEscape(d.Locale) + "/latest.html"
}

func (c *r2Client) put(ctx context.Context, d description) error {
	body := []byte(d.HTML) // Go strings and Python's .encode("utf-8") agree.
	digest := sha256.Sum256(body)
	payloadHash := hex.EncodeToString(digest[:])
	url := objectURL(c.endpoint, c.bucket, d)
	var last error
	for attempt := 1; attempt <= 2; attempt++ {
		req, err := http.NewRequestWithContext(ctx, http.MethodPut, url, bytes.NewReader(body))
		if err != nil {
			return err
		}
		req.Header.Set("Content-Type", "text/html")
		req.Header.Set("Cache-Control", "public, max-age=86400")
		req.Header.Set("X-Amz-Content-Sha256", payloadHash)
		// Sign each attempt with fresh time, as the Python uploader does.
		if err := c.signer.SignHTTP(ctx, c.creds, req, payloadHash, "s3", "auto", time.Now()); err != nil {
			return err
		}
		resp, err := c.http.Do(req)
		if err == nil {
			_, _ = io.Copy(io.Discard, io.LimitReader(resp.Body, 1024))
			_ = resp.Body.Close()
			if resp.StatusCode >= 200 && resp.StatusCode < 300 {
				return nil
			}
			last = &putError{status: resp.StatusCode, err: fmt.Errorf("R2 PUT returned HTTP %d", resp.StatusCode)}
			if !retryableStatus(resp.StatusCode) {
				return last
			}
		} else {
			last = &putError{err: err}
		}
		if attempt == 2 || ctx.Err() != nil {
			break
		}
		ceiling := 500 * time.Millisecond
		delay := ceiling/2 + time.Duration(rand.Int63n(int64(ceiling/2)))
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(delay):
		}
	}
	return last
}

func retryableStatus(status int) bool {
	switch status {
	case 408, 425, 429, 500, 502, 503, 504:
		return true
	default:
		return false
	}
}
