package worker

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/cookiejar"

	jsonld "github.com/colophon-group/jobseek/apps/crawler/go/jsonld-detail"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	workday "github.com/colophon-group/jobseek/apps/crawler/go/workday-monitor"
)

func fetchJSONLDWithWorkdayRecovery(ctx context.Context, verified *VerifiedHTTP, primary *http.Client, profile queue.WorkdayDetailProfile) (jsonld.FetchResult, error) {
	result, primaryError := jsonld.FetchDetailWithClient(ctx, jsonld.Request{URL: profile.SourceURL, Config: profile.JSONLDConfig}, primary)
	config := profile.JSONLDRecovery
	if config == nil || primaryError == nil || result.Status != 403 || result.ErrorKind != "status" {
		return result, primaryError
	}
	fallback := verified
	if config.Proxy != verified.proxyRequired {
		if config.Proxy {
			fallback = verified.workdayRecoveryProxy
		} else {
			fallback = verified.workdayRecoveryDirect
		}
	}
	if fallback == nil || fallback.client == nil || fallback.proxyRequired != config.Proxy || fallback.skipSSL {
		return result, queue.ErrConfiguration
	}
	client := *fallback.client
	jar, err := cookiejar.New(nil)
	if err != nil {
		return result, err
	}
	client.Jar = jar
	recovered, err := workday.RecoverPhenomDetailWithClient(ctx, profile.SourceURL, *config, &client)
	result.Requests += recovered.Requests
	result.Responses += recovered.Responses
	result.Bytes += int(recovered.Bytes)
	if err != nil {
		var reserved *workday.ReservationError
		if errors.As(err, &reserved) {
			result.ErrorKind = "tdm"
			result.TDMSource = "header"
			result.TDMPolicy = reserved.PolicyURL
			result.FinalURL = reserved.URL
			return result, err
		}
		var failed *workday.FetchError
		if errors.As(err, &failed) {
			result.ErrorKind = failed.Kind
			if failed.Kind == "http" || failed.Kind == "" && failed.Status != 0 {
				result.ErrorKind = "status"
			}
			result.Status = failed.Status
			result.FinalURL = failed.URL
		}
		var detail *workday.DetailFetchError
		if errors.As(err, &detail) {
			result.ErrorKind = detail.Kind
			if detail.Kind == "http" {
				result.ErrorKind = "status"
			}
			result.Status = detail.Status
			result.FinalURL = recovered.URL
		}
		return result, err
	}
	if !recovered.Found {
		return result, primaryError
	}
	body, err := json.Marshal(recovered.Detail.Content)
	if err != nil {
		return result, err
	}
	if err = json.Unmarshal(body, &result.Content); err != nil {
		return result, err
	}
	result.Status = recovered.Detail.Status
	result.FinalURL = recovered.URL
	result.ErrorKind = ""
	result.TDMSource = ""
	result.TDMPolicy = ""
	return result, nil
}
