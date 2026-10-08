package worker

import (
	"context"
	"errors"
	"net/http"
	"net/http/cookiejar"
	"strings"

	"github.com/andybalholm/cascadia"
	jsonld "github.com/colophon-group/jobseek/apps/crawler/go/jsonld-detail"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	"golang.org/x/net/html"
)

type sfDetailScope struct {
	profile queue.GreenhouseMonitorProfile
}

func (s sfDetailScope) ResourceMatches(u string) bool {
	return queue.RSSDetailMonitorResourceMatches(s.profile, u)
}
func (s sfDetailScope) HonorContentLength() bool { return true }

func enrichRSSDetailFields(ctx context.Context, verified *http.Client, profile queue.GreenhouseMonitorProfile, config map[string]string, inventory RichDiscovery) (RichDiscovery, error) {
	fields, required, err := queue.RSSDetailFields(config)
	if err != nil || verified == nil {
		return RichDiscovery{}, queue.ErrConfiguration
	}
	rules, err := queue.FeedMonitorURLRules(config)
	if err != nil {
		return RichDiscovery{}, err
	}
	client := *verified
	client.Jar, err = cookiejar.New(nil)
	if err != nil {
		return RichDiscovery{}, err
	}
	client.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	fail := func(err error) (RichDiscovery, error) { return RichDiscovery{Response: inventory.Response}, err }
	for i := range inventory.Jobs {
		job := &inventory.Jobs[i]
		keep, err := rules.FilterURL(job.URL)
		if err != nil {
			return fail(err)
		}
		if !keep {
			continue
		}
		if !queue.RSSDetailMonitorResourceMatches(profile, job.URL) {
			return fail(queue.ErrConfiguration)
		}
		tree, response, err := fetchSFDetailProperties(ctx, &client, profile, job.URL)
		if response != nil {
			inventory.Response = response
		}
		if err != nil {
			return fail(err)
		}
		metadata := map[string]any{}
		for key, value := range job.Metadata {
			metadata[key] = value
		}
		for key, property := range fields {
			node := cascadia.Query(tree, cascadia.MustCompile(`[data-careersite-propertyid="`+property+`"]`))
			var text strings.Builder
			var visit func(*html.Node)
			visit = func(n *html.Node) {
				if n.Type == html.TextNode {
					text.WriteString(strings.TrimSpace(n.Data))
				}
				for c := n.FirstChild; c != nil; c = c.NextSibling {
					visit(c)
				}
			}
			if node != nil {
				visit(node)
			}
			value := strings.TrimSpace(text.String())
			if value != "" {
				metadata[key] = value
			} else if required[key] {
				return fail(errors.New("required SuccessFactors detail property missing"))
			}
		}
		if len(metadata) > 0 {
			job.Metadata = metadata
		}
	}
	return inventory, nil
}

func fetchSFDetailProperties(ctx context.Context, client *http.Client, profile queue.GreenhouseMonitorProfile, endpoint string) (*html.Node, *GreenhouseResponse, error) {
	current := endpoint
	visited := map[string]bool{endpoint: true}
	redirects := 0
	var observed *GreenhouseResponse
	for contentAttempt := 0; contentAttempt < 3; contentAttempt++ {
		var body []byte
		for attempt := 0; attempt < 3; {
			raw, response, err := fetchProviderStatusResource(ctx, client, sfDetailScope{profile}, current, nil, nil, 5_000_000, nil)
			observed = response
			if response != nil && (response.status == 301 || response.status == 302 || response.status == 303 || response.status == 307 || response.status == 308) {
				next, joinErr := pythonJoinURL(current, response.location)
				if joinErr != nil || response.location == "" || redirects >= 3 || visited[next] || !queue.RSSDetailMonitorResourceMatches(profile, next) {
					return nil, observed, queue.ErrConfiguration
				}
				visited[next] = true
				redirects++
				current = next
				continue
			}
			if err == nil && len(raw) > 0 {
				body = raw
				break
			}
			if response != nil && response.reserved || ctx.Err() != nil {
				return nil, observed, err
			}
			if response != nil && response.status != 200 && response.status != 202 && response.status != 401 && response.status != 403 && response.status != 429 && response.status < 500 {
				return nil, observed, err
			}
			if attempt == 2 {
				if err == nil {
					err = errors.New("empty SuccessFactors detail response")
				}
				return nil, observed, err
			}
			if err = almaRetry(ctx, attempt); err != nil {
				return nil, observed, err
			}
			attempt++
		}
		tree, err := html.Parse(strings.NewReader(jsonld.DecodeDocument(body, observed.contentType)))
		if err == nil && cascadia.Query(tree, cascadia.MustCompile(`[data-careersite-propertyid="title"]`)) != nil {
			return tree, observed, nil
		}
		if contentAttempt < 2 {
			if err = almaRetry(ctx, contentAttempt); err != nil {
				return nil, observed, err
			}
		}
	}
	return nil, observed, errors.New("SuccessFactors detail job marker missing")
}
