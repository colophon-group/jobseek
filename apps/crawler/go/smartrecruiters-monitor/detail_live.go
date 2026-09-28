package smartrecruiters

import (
	"context"
	"errors"
	"net/url"
	"strings"
)

type DetailResult struct {
	Content   Object   `json:"content"`
	Requests  int      `json:"requests"`
	Responses int      `json:"responses"`
	Bytes     int64    `json:"bytes"`
	Status    int      `json:"status"`
	FinalURL  string   `json:"final_url"`
	Failure   *Failure `json:"failure,omitempty"`
	Error     string   `json:"error,omitempty"`
}

// DetailEndpoint preserves ordinary and oneclick publication IDs, including
// slugs. Only the selected public API tenant is contacted.
func DetailEndpoint(raw string) (string, string, error) {
	u, err := url.Parse(raw)
	if err != nil || u.Scheme != "https" || (u.Host != "jobs.smartrecruiters.com" && u.Host != "careers.smartrecruiters.com") || u.User != nil {
		return "", "", errors.New("unsupported SmartRecruiters detail URL")
	}
	parts := strings.Split(strings.Trim(u.Path, "/"), "/")
	var token, id string
	if len(parts) == 5 && parts[0] == "oneclick-ui" && parts[1] == "company" && (parts[3] == "publication" || parts[3] == "job") {
		token, id = parts[2], parts[4]
	} else if len(parts) == 2 {
		token, id = parts[0], parts[1]
	}
	if !tokenRE.MatchString(token) || !detailPathID.MatchString(id) {
		return "", "", errors.New("unsupported SmartRecruiters detail identity")
	}
	return token, ListURL(token) + "/" + id, nil
}

func FetchDetail(ctx context.Context, raw string) (DetailResult, error) {
	return fetchDetailWith(ctx, raw, newClient())
}

// FetchDetailForBoard can retain the existing response on exact selected boards.
// Capture never sends another request or changes the extraction result.
func FetchDetailForBoard(ctx context.Context, raw, boardID string) (DetailResult, error) {
	return fetchDetailRetained(ctx, raw, newClient(), detailCapture(boardID, raw, "/tmp"))
}

func fetchDetailWith(ctx context.Context, raw string, client requestDoer) (DetailResult, error) {
	return fetchDetailRetained(ctx, raw, client, nil)
}

func fetchDetailRetained(ctx context.Context, raw string, client requestDoer, retain func(string, []byte)) (DetailResult, error) {
	result := DetailResult{}
	token, endpoint, err := DetailEndpoint(raw)
	if err == nil {
		result.FinalURL = endpoint
		f := &fetcher{token: token, client: client, retain: retain}
		var data Object
		// The retained Python detail scraper makes one request and returns empty
		// content for every non-200 status. Do not apply monitor retry semantics.
		data, result.Status, _, err = f.once(ctx, endpoint, DetailResponseMaxBytes)
		result.Requests, result.Responses, result.Bytes = f.result.Requests, f.result.Responses, f.result.Bytes
		if err == nil && result.Status == 200 {
			var job Job
			job, err = ParseDetail(data)
			if err == nil {
				result.Content = Object{"title": job.Title, "description": job.Description, "locations": job.Locations,
					"employment_type": job.EmploymentType, "job_location_type": job.JobLocationType,
					"date_posted": job.DatePosted, "base_salary": job.BaseSalary, "metadata": job.Metadata}
			}
		}
	}
	if err != nil {
		result.Content = nil
		result.Error = err.Error()
		var failure *Failure
		if errors.As(err, &failure) {
			result.Failure = failure
		}
	}
	return result, err
}
