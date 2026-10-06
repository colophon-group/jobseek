module github.com/colophon-group/jobseek/pilots/go-lightpanda

go 1.26.0

require (
	github.com/chromedp/cdproto v0.0.0-20250724212937-08a3db8b4327
	github.com/chromedp/chromedp v0.14.2
	github.com/colophon-group/jobseek/apps/crawler/contracts v0.0.0
	google.golang.org/protobuf v1.36.10
)

require (
	github.com/andybalholm/cascadia v1.3.3 // indirect
	github.com/colophon-group/jobseek/apps/crawler/go/dom-detail v0.0.0 // indirect
	github.com/colophon-group/jobseek/apps/crawler/go/jsonld-detail v0.0.0 // indirect
	github.com/dlclark/regexp2/v2 v2.8.0 // indirect
	github.com/jmespath/go-jmespath v0.4.0 // indirect
	golang.org/x/net v0.59.0 // indirect
	golang.org/x/text v0.42.0 // indirect
)

require (
	github.com/chromedp/sysutil v1.1.0 // indirect
	github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor v0.0.0
	github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy v0.0.0
	github.com/go-json-experiment/json v0.0.0-20250725192818-e39067aee2d2 // indirect
	github.com/gobwas/httphead v0.1.0 // indirect
	github.com/gobwas/pool v0.2.1 // indirect
	github.com/gobwas/ws v1.4.0 // indirect
	golang.org/x/sys v0.48.0 // indirect
)

replace github.com/colophon-group/jobseek/apps/crawler/contracts => ../../apps/crawler/contracts

replace github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor => ../../apps/crawler/go/api-sniffer-monitor

replace github.com/colophon-group/jobseek/apps/crawler/go/dom-detail => ../../apps/crawler/go/dom-detail

replace github.com/colophon-group/jobseek/apps/crawler/go/jsonld-detail => ../../apps/crawler/go/jsonld-detail

replace github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy => ../../apps/crawler/go/publisher-policy
