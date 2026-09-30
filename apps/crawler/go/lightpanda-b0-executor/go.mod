module github.com/colophon-group/jobseek/apps/crawler/go/lightpanda-b0-executor

go 1.26.0

require (
	github.com/jackc/pgx/v5 v5.9.2
	google.golang.org/protobuf v1.36.10
)

require (
	github.com/andybalholm/cascadia v1.3.3 // indirect
	github.com/cespare/xxhash/v2 v2.3.0 // indirect
	github.com/dgryski/go-rendezvous v0.0.0-20200823014737-9f7001d12a5f // indirect
	github.com/dlclark/regexp2/v2 v2.8.0 // indirect
	github.com/dustin/go-humanize v1.0.1 // indirect
	github.com/google/uuid v1.6.0 // indirect
	github.com/mattn/go-isatty v0.0.24 // indirect
	github.com/ncruces/go-strftime v1.0.0 // indirect
	github.com/remyoudompheng/bigfft v0.0.0-20230129092748-24d4a6f8daec // indirect
	golang.org/x/net v0.59.0 // indirect
	golang.org/x/sys v0.48.0 // indirect
	modernc.org/libc v1.77.1 // indirect
	modernc.org/mathutil v1.7.1 // indirect
	modernc.org/memory v1.12.1 // indirect
	modernc.org/sqlite v1.60.1 // indirect
)

require (
	github.com/colophon-group/jobseek/apps/crawler/contracts v0.0.0
	github.com/colophon-group/jobseek/apps/crawler/go/dom-detail v0.0.0
	github.com/colophon-group/jobseek/apps/crawler/go/job-enrichment v0.0.0
	github.com/colophon-group/jobseek/apps/crawler/go/jsonld-detail v0.0.0
	github.com/jackc/pgpassfile v1.0.0 // indirect
	github.com/jackc/pgservicefile v0.0.0-20240606120523-5a60cdf6a761 // indirect
	github.com/jackc/puddle/v2 v2.2.2 // indirect
	golang.org/x/sync v0.23.0 // indirect
	golang.org/x/text v0.42.0 // indirect
)

replace github.com/colophon-group/jobseek/apps/crawler/contracts => ../../contracts

replace github.com/colophon-group/jobseek/apps/crawler/go/job-enrichment => ../job-enrichment

replace github.com/colophon-group/jobseek/apps/crawler/go/dom-detail => ../dom-detail

replace github.com/colophon-group/jobseek/apps/crawler/go/jsonld-detail => ../jsonld-detail

require (
	github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy v0.0.0
	github.com/redis/go-redis/v9 v9.14.0
)

replace github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy => ../publisher-policy
