package worker

import (
	"context"
	"net/http"

	apisniffer "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	jsonld "github.com/colophon-group/jobseek/apps/crawler/go/jsonld-detail"
	executor "github.com/colophon-group/jobseek/apps/crawler/go/lightpanda-b0-executor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func fetchEmbeddedDetail(ctx context.Context, client *http.Client, profile queue.WorkdayDetailProfile) (jsonld.FetchResult, error) {
	result := jsonld.FetchResult{}
	if apisniffer.ValidateEmbeddedDetail(profile.EmbeddedConfig, profile.EmbeddedNextdata) != nil {
		return result, queue.ErrConfiguration
	}
	doc, err := jsonld.FetchDocumentWithClient(ctx, profile.SourceURL, jsonld.DocumentOptions{}, client)
	result.Requests, result.Responses, result.Bytes = doc.Requests, doc.Responses, doc.Bytes
	result.Status, result.FinalURL = doc.Status, doc.FinalURL
	result.ErrorKind, result.TDMSource, result.TDMPolicy = doc.ErrorKind, doc.TDMSource, doc.TDMPolicy
	if err != nil {
		return result, err
	}
	if doc.Status != 200 {
		// The embedded Python scraper returns empty content for every non-200
		// response. Preserve its recurring failure policy rather than adopting
		// DOM/JSON-LD's permanent navigation-gone classification.
		result.ErrorKind = "empty"
		return result, executor.ErrEmptyResult
	}
	if ctx.Err() != nil {
		return result, ctx.Err()
	}
	result.Content, err = apisniffer.ProjectEmbeddedDetail(jsonld.DecodeDocument(doc.Body, doc.ContentType), profile.EmbeddedConfig, profile.EmbeddedNextdata)
	if err != nil {
		return result, executor.ErrEmptyResult
	}
	return result, nil
}
