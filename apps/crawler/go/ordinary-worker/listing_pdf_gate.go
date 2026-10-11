package worker

import (
	"bytes"
	"context"
	"errors"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"time"
	"unicode/utf8"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

type listingPDFResource struct{ initial *url.URL }

func (listingPDFResource) AcceptSuccess2xx() bool { return true }

func (s listingPDFResource) ResourceMatches(raw string) bool {
	u, e := url.Parse(raw)
	return e == nil && len(raw) <= 8192 && u.User == nil && pdfSameOrigin(s.initial, u)
}

func extractListingPDFText(ctx context.Context, body []byte) (string, error) {
	if len(body) > 20<<20 || !bytes.HasPrefix(bytes.TrimSpace(body), []byte("%PDF")) {
		return "", dom.ErrPDFProof
	}
	dir, e := os.MkdirTemp("", "jobseek-pdf-listing-")
	if e != nil {
		return "", e
	}
	defer os.RemoveAll(dir)
	file := filepath.Join(dir, "source.pdf")
	if e = os.WriteFile(file, body, 0600); e != nil {
		return "", e
	}
	bounded, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
	info, e := pdfCommand(bounded, 64<<10, "pdfinfo", file)
	if e != nil {
		return "", e
	}
	match := regexp.MustCompile(`(?m)^Pages:\s+([0-9]+)\s*$`).FindSubmatch(info)
	if len(match) != 2 {
		return "", dom.ErrPDFProof
	}
	pages, e := strconv.Atoi(string(match[1]))
	if e != nil || pages > 200 || pages < 1 {
		return "", dom.ErrPDFProof
	}
	text, e := pdfCommand(bounded, 8_000_000, "pdftotext", "-enc", "UTF-8", "-nopgbrk", file, "-")
	if e != nil {
		return "", e
	}
	if !utf8.Valid(text) || utf8.RuneCount(text) > 2_000_000 {
		return "", dom.ErrPDFProof
	}
	return strings.TrimSpace(string(text)), nil
}

func fetchListingPDFText(ctx context.Context, client *http.Client, p queue.GreenhouseMonitorProfile, config map[string]string, source string, headers map[string]string) (string, *GreenhouseResponse, bool, error) {
	initial, e := url.Parse(source)
	if e != nil || len(source) > 8192 || initial.User != nil || initial.Scheme != "https" && initial.Scheme != "http" {
		return "", nil, false, dom.ErrPDFProof
	}
	if len(headers) > 0 {
		board, e := url.Parse(config["board_url"])
		if e != nil || !pdfSameOrigin(board, initial) {
			return "", nil, false, dom.ErrPDFProof
		}
	}
	sealed := *client
	sealed.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	if len(headers) > 0 {
		sealed.Jar = nil
	}
	initial.Fragment = ""
	current := initial.String()
	sourcePDF := strings.HasSuffix(strings.ToLower(initial.Path), ".pdf")
	scope := listingPDFResource{initial}
	var observed *GreenhouseResponse
	for hop := 0; hop <= 5; hop++ {
		request := api.Request{Method: "GET", URL: current, Headers: http.Header{}}
		for k, v := range headers {
			request.Headers.Set(k, v)
		}
		body, h, r, e := fetchLastHTTPOnce(ctx, &sealed, scope, request, 20<<20)
		if r != nil {
			r.endpoint = source
			r.domVerification = &domVerificationBinding{p.Endpoint, p.BoardID, p.EffectiveConfigSHA256, source, domVerificationConfig(config)}
			observed = r
		}
		if r != nil && r.reserved {
			return "", r, false, e
		}
		if ctx.Err() != nil {
			return "", observed, false, ctx.Err()
		}
		if r == nil {
			return "", observed, false, e
		}
		if r.status == 404 || r.status == 410 {
			return "", observed, true, nil
		}
		if r.status == 301 || r.status == 302 || r.status == 303 || r.status == 307 || r.status == 308 {
			if hop == 5 || h.Get("Location") == "" {
				return "", observed, false, dom.ErrPDFProof
			}
			base, _ := url.Parse(current)
			ref, e := url.Parse(h.Get("Location"))
			if e != nil {
				return "", observed, false, dom.ErrPDFProof
			}
			next := base.ResolveReference(ref)
			next.Fragment = ""
			next.RawFragment = ""
			if !scope.ResourceMatches(next.String()) {
				return "", observed, false, dom.ErrPDFProof
			}
			current = next.String()
			continue
		}
		if e != nil {
			return "", observed, false, e
		}
		final, _ := url.Parse(current)
		if !sourcePDF && (!strings.HasSuffix(strings.ToLower(final.Path), ".pdf") || !strings.EqualFold(strings.TrimSpace(strings.Split(h.Get("Content-Type"), ";")[0]), "application/pdf")) {
			return "", observed, false, dom.ErrPDFProof
		}
		if raw := h.Get("Content-Length"); raw != "" {
			n, e := strconv.ParseInt(raw, 10, 64)
			if e != nil || n < 0 || n > 20<<20 {
				return "", observed, false, dom.ErrPDFProof
			}
		}
		text, e := extractListingPDFText(ctx, body)
		return text, observed, false, e
	}
	return "", observed, false, dom.ErrPDFProof
}

func filterListingPDFInventory(ctx context.Context, client *http.Client, p queue.GreenhouseMonitorProfile, config map[string]string, in RichDiscovery, proof *dom.PDFListingProof, headers map[string]string, now time.Time) (RichDiscovery, error) {
	if proof == nil {
		return in, nil
	}
	if client == nil || len(in.Jobs) > 100 {
		in.Jobs = nil
		return in, dom.ErrPDFProof
	}
	jobs := append([]RichMonitorJob{}, in.Jobs...)
	in.Jobs = nil
	kept := make([]bool, len(jobs))
	removed := make([]bool, len(jobs))
	expired := make([]bool, len(jobs))
	deadlines := make([]string, len(jobs))
	failures := make([]error, len(jobs))
	responses := make([]*GreenhouseResponse, len(jobs))
	work, cancel := context.WithCancel(ctx)
	defer cancel()
	tasks := make(chan int)
	var wg sync.WaitGroup
	for n := 0; n < min(4, len(jobs)); n++ {
		wg.Go(func() {
			for i := range tasks {
				if work.Err() != nil {
					failures[i] = work.Err()
					continue
				}
				var text string
				text, responses[i], removed[i], failures[i] = fetchListingPDFText(work, client, p, config, jobs[i].URL, headers)
				if failures[i] == nil && !removed[i] {
					kept[i], deadlines[i], failures[i] = proof.ClassifyCurrentness(text, now)
					expired[i] = !kept[i] && failures[i] == nil && deadlines[i] != ""
					if failures[i] == nil && kept[i] {
						kept[i], failures[i] = proof.OwnershipKeeps(text)
					}
				}
				if failures[i] != nil {
					cancel()
				}
			}
		})
	}
	for i := range jobs {
		select {
		case tasks <- i:
		case <-work.Done():
			failures[i] = work.Err()
		}
	}
	close(tasks)
	wg.Wait()
	for _, r := range responses {
		if r != nil && r.reserved {
			in.Response = r
			return in, &DiscoveryError{Kind: "publisher_reserved", Status: r.status}
		}
	}
	for _, e := range failures {
		if e != nil && !errors.Is(e, context.Canceled) {
			return in, e
		}
	}
	if ctx.Err() != nil {
		return in, ctx.Err()
	}
	for _, e := range failures {
		if e != nil {
			return in, e
		}
	}
	allDates := len(jobs) > 0
	in.Jobs = []RichMonitorJob{}
	for i, j := range jobs {
		allDates = allDates && !removed[i] && expired[i]
		if kept[i] {
			if p.Provider == "api_sniffer" && deadlines[i] != "" {
				extras := map[string]any{}
				for k, v := range j.Extras {
					extras[k] = v
				}
				extras["valid_through"] = deadlines[i]
				j.Extras = extras
			}
			in.Jobs = append(in.Jobs, j)
		}
	}
	if p.Provider == "dom" && allDates && len(in.Jobs) == 0 {
		in.VerifiedEmptyReason = "all discovered PDFs are outside their parsed application periods"
	}
	return in, nil
}

func pdfVerificationResponseMatches(p queue.GreenhouseMonitorProfile, config map[string]string, r *GreenhouseResponse) bool {
	if r == nil || r.domVerification == nil || p.Provider != "api_sniffer" {
		return false
	}
	o, e := queue.APISnifferMonitorOptions(config)
	b := r.domVerification
	return e == nil && o.PDFProof != nil && p.Endpoint == o.Endpoint && b.parent == p.Endpoint && b.boardID == p.BoardID && b.effective == p.EffectiveConfigSHA256 && b.resource == r.endpoint && b.config == domVerificationConfig(config)
}
