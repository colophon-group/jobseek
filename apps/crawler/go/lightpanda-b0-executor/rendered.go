package executor

import (
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"net/url"
	"strconv"
	"strings"
	"unicode/utf8"

	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	publisherpolicy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
)

const (
	HTMLLimit      = 2_000_000
	HTMLChunkLimit = 64 * 1024
)

var ErrRenderedResult = errors.New("rendered result rejected")

// NavigationHTTPError is classified only after the complete HTML manifest has
// been verified. A gone status cannot bypass content integrity validation.
type NavigationHTTPError struct {
	RequestedURL string
	ResponseURL  string
	Status       uint32
}

func (e *NavigationHTTPError) Error() string       { return "browser navigation HTTP status" }
func (e *NavigationHTTPError) PermanentGone() bool { return e.Status == 404 || e.Status == 410 }

func RenderedHTML(result *runtimev1.BrowserResult, requestedURL string) (string, error) {
	if result == nil {
		return "", ErrRenderedResult
	}
	success := result.GetSuccess()
	if success == nil || success.Status == nil || success.GetStatus() < 100 || success.GetStatus() > 599 || success.Html == nil ||
		len(success.ActionOutcomes) != 0 || len(success.Captures) != 0 || len(success.Evaluations) != 0 || len(success.Artifacts) != 0 || !validRenderedURL(success.FinalUrl) {
		return "", ErrRenderedResult
	}
	manifest := success.Html
	if !manifest.Complete || manifest.TotalSizeBytes > HTMLLimit || !hexDigest.MatchString(manifest.TotalSha256) ||
		uint64(len(manifest.Chunks)) != (manifest.TotalSizeBytes+HTMLChunkLimit-1)/HTMLChunkLimit {
		return "", ErrRenderedResult
	}
	body := make([]byte, 0, int(manifest.TotalSizeBytes))
	for sequence, chunk := range manifest.Chunks {
		if chunk == nil || chunk.Sequence != uint32(sequence) {
			return "", ErrRenderedResult
		}
		inline, ok := chunk.Storage.(*runtimev1.DataChunk_InlineBody)
		if !ok {
			return "", ErrRenderedResult
		}
		expectedSize := min(uint64(HTMLChunkLimit), manifest.TotalSizeBytes-uint64(len(body)))
		piece := inline.InlineBody
		digest := sha256.Sum256(piece)
		if uint64(len(piece)) != expectedSize || chunk.SizeBytes != expectedSize || !hexDigest.MatchString(chunk.Sha256) || hex.EncodeToString(digest[:]) != chunk.Sha256 || len(piece) > HTMLLimit-len(body) {
			return "", ErrRenderedResult
		}
		body = append(body, piece...)
	}
	digest := sha256.Sum256(body)
	if uint64(len(body)) != manifest.TotalSizeBytes || hex.EncodeToString(digest[:]) != manifest.TotalSha256 || !utf8.Valid(body) {
		return "", ErrRenderedResult
	}
	if err := publisherpolicy.Check(success.ResourcePolicy, string(body), requestedURL); err != nil {
		return "", err
	}
	if success.GetStatus() >= 400 {
		return "", &NavigationHTTPError{RequestedURL: requestedURL, ResponseURL: success.FinalUrl, Status: success.GetStatus()}
	}
	return string(body), nil
}

func validRenderedURL(value string) bool {
	// The Go supervisor already rejects malformed URL escapes and whitespace
	// before dispatch. This keeps the admitted lane's existing URL subset.
	if value == "" || len(value) > 8192 || strings.ContainsAny(value, " \t\r\n") {
		return false
	}
	parsed, err := url.Parse(value)
	if err != nil || (parsed.Scheme != "http" && parsed.Scheme != "https") || parsed.Hostname() == "" || parsed.User != nil || parsed.Fragment != "" {
		return false
	}
	if port := parsed.Port(); port != "" {
		n, err := strconv.ParseUint(port, 10, 16)
		if err != nil || n > 65535 {
			return false
		}
	}
	return true
}
