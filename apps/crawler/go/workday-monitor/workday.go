// Package workday implements the bounded, single-site Workday monitor slice.
// The caller supplies the POST transport so the production publisher policy
// remains authoritative when this slice is wired into the worker.
package workday

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"regexp"
)

const (
	pageSize  = 20
	resultCap = 2000
)

var ErrUnsupportedInventory = errors.New("Workday inventory requires a faceted or deep-pagination strategy")

var (
	companyToken  = regexp.MustCompile(`^[A-Za-z0-9_-]+$`)
	instanceToken = regexp.MustCompile(`^wd[0-9]+$`)
	siteToken     = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$`)
)

// Poster executes one policy-approved Workday list POST. The supplied body is
// already encoded; implementations must return the complete response body or
// an error and must not follow a 303 as a GET.
type Poster func(ctx context.Context, url string, body []byte) ([]byte, error)

type Site struct {
	Company  string
	Instance string
	Name     string
}

type Result struct {
	URLs       []string
	Advertised int
	Requests   int
	Recovered  bool
}

type listRequest struct {
	Limit  int `json:"limit"`
	Offset int `json:"offset"`
}

type listResponse struct {
	Total       *int `json:"total"`
	JobPostings []struct {
		ExternalPath string `json:"externalPath"`
	} `json:"jobPostings"`
}

// DiscoverSingleSite matches the Python monitor's ordinary, unfaceted
// pagination for a configured all_sites=false origin. It fails closed at the
// Workday 2,000-result cap and on a materially incomplete inventory. No
// additional source requests are made during replay; this function becomes
// live only after an exclusive origin cutover.
func DiscoverSingleSite(ctx context.Context, site Site, post Poster) (Result, error) {
	if !companyToken.MatchString(site.Company) || !instanceToken.MatchString(site.Instance) || !siteToken.MatchString(site.Name) || post == nil {
		return Result{}, errors.New("valid Workday site and poster are required")
	}
	listURL := fmt.Sprintf("https://%s.%s.myworkdayjobs.com/wday/cxs/%s/%s/jobs", site.Company, site.Instance, site.Company, site.Name)
	jobPrefix := fmt.Sprintf("https://%s.%s.myworkdayjobs.com/%s", site.Company, site.Instance, site.Name)
	result := Result{}
	paths := make([]string, 0)
	seen := make(map[string]struct{})

	fetch := func(offset int) (listResponse, error) {
		body, err := json.Marshal(listRequest{Limit: pageSize, Offset: offset})
		if err != nil {
			return listResponse{}, err
		}
		response, err := post(ctx, listURL, body)
		result.Requests++
		if err != nil {
			return listResponse{}, err
		}
		var page listResponse
		if err := json.Unmarshal(response, &page); err != nil {
			return listResponse{}, fmt.Errorf("decode Workday page at offset %d: %w", offset, err)
		}
		if offset == 0 && (page.Total == nil || *page.Total < 0) {
			return listResponse{}, fmt.Errorf("invalid Workday total at offset %d", offset)
		}
		return page, nil
	}

	appendPaths := func(page listResponse) {
		for _, posting := range page.JobPostings {
			path := posting.ExternalPath
			if path == "" {
				continue
			}
			if _, exists := seen[path]; exists {
				continue
			}
			seen[path] = struct{}{}
			paths = append(paths, path)
		}
	}

	page, err := fetch(0)
	if err != nil {
		return Result{}, err
	}
	result.Advertised = *page.Total
	if result.Advertised >= resultCap {
		return Result{}, ErrUnsupportedInventory
	}
	appendPaths(page)
	offset := len(page.JobPostings)
	for offset < result.Advertised && len(page.JobPostings) > 0 {
		page, err = fetch(offset)
		if err != nil {
			return Result{}, err
		}
		appendPaths(page)
		offset += len(page.JobPostings)
	}

	if materiallyShort(len(paths), result.Advertised) {
		// Python reconciles one bounded direct pass after duplicate rows or
		// normal in-crawl churn. Preserve that behavior before failing closed.
		result.Recovered = true
		offset = 0
		for offset < result.Advertised {
			page, err = fetch(offset)
			if err != nil {
				return Result{}, err
			}
			if len(page.JobPostings) == 0 {
				break
			}
			appendPaths(page)
			offset += len(page.JobPostings)
		}
	}
	if materiallyShort(len(paths), result.Advertised) {
		return Result{}, fmt.Errorf("Workday pagination returned %d of %d advertised unique jobs", len(paths), result.Advertised)
	}
	result.URLs = make([]string, len(paths))
	for index, path := range paths {
		result.URLs[index] = jobPrefix + path
	}
	return result, nil
}

func materiallyShort(discovered, advertised int) bool {
	tolerance := (advertised + 99) / 100
	if tolerance < 1 {
		tolerance = 1
	}
	return discovered < advertised-tolerance
}
