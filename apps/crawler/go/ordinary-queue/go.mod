module github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue

go 1.26.0

require github.com/colophon-group/jobseek/apps/crawler/go/oracle-hcm v0.0.0

replace github.com/colophon-group/jobseek/apps/crawler/go/oracle-hcm => ../oracle-hcm

require github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor v0.0.0

replace github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor => ../api-sniffer-monitor

require github.com/redis/go-redis/v9 v9.14.0

require github.com/colophon-group/jobseek/apps/crawler/go/workday-monitor v0.0.0

replace github.com/colophon-group/jobseek/apps/crawler/go/workday-monitor => ../workday-monitor

require (
	github.com/andybalholm/cascadia v1.3.3 // indirect
	github.com/jackc/pgpassfile v1.0.0 // indirect
	github.com/jackc/pgservicefile v0.0.0-20240606120523-5a60cdf6a761 // indirect
	github.com/jackc/puddle/v2 v2.2.2 // indirect
	github.com/jmespath/go-jmespath v0.4.0 // indirect
	golang.org/x/net v0.59.0 // indirect
	golang.org/x/sync v0.23.0 // indirect
	golang.org/x/text v0.42.0 // indirect
)

require (
	github.com/cespare/xxhash/v2 v2.3.0 // indirect
	github.com/dgryski/go-rendezvous v0.0.0-20200823014737-9f7001d12a5f // indirect
	github.com/jackc/pgx/v5 v5.9.2
)

replace github.com/colophon-group/jobseek/apps/crawler/contracts => ../../contracts

require (
	github.com/colophon-group/jobseek/apps/crawler/go/join-monitor v0.0.0
	github.com/colophon-group/jobseek/apps/crawler/go/jsonld-detail v0.0.0
	github.com/colophon-group/jobseek/apps/crawler/go/smartrecruiters-monitor v0.0.0
	github.com/colophon-group/jobseek/apps/crawler/go/workable-monitor v0.0.0
)

replace github.com/colophon-group/jobseek/apps/crawler/go/jsonld-detail => ../jsonld-detail

replace github.com/colophon-group/jobseek/apps/crawler/go/smartrecruiters-monitor => ../smartrecruiters-monitor

replace github.com/colophon-group/jobseek/apps/crawler/go/workable-monitor => ../workable-monitor

replace github.com/colophon-group/jobseek/apps/crawler/go/join-monitor => ../join-monitor

require github.com/colophon-group/jobseek/apps/crawler/go/dom-detail v0.0.0

replace github.com/colophon-group/jobseek/apps/crawler/go/dom-detail => ../dom-detail

require (
	github.com/colophon-group/jobseek/apps/crawler/contracts v0.0.0
	github.com/colophon-group/jobseek/apps/crawler/go/sitemap-monitor v0.0.0
	github.com/dlclark/regexp2/v2 v2.8.0
)

replace github.com/colophon-group/jobseek/apps/crawler/go/sitemap-monitor => ../sitemap-monitor
