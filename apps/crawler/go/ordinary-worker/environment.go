package worker

import (
	"crypto/sha256"
	_ "embed"
	"encoding/hex"
	"encoding/json"
	"errors"
	"math"
	"net"
	"net/url"
	"path/filepath"
	"regexp"
	"runtime/debug"
	"strconv"
	"strings"
	"time"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

// This snapshot matches certifi in uv.lock and is independent of Python at runtime.
//
//go:embed trust/certifi-2026.2.25.pem
var pinnedCA []byte

const pinnedCASHA256 = "fc9165a12403263e7ebfbdad7be7a3eac0fa5d325d3c70465f28d3690072ca28"

var sourcePattern = regexp.MustCompile(`^[0-9a-f]{40}$`)
var planPattern = regexp.MustCompile(`^[0-9a-f]{64}$`)
var ErrStartup = errors.New("ordinary worker startup rejected")
var ErrDrainTimeout = errors.New("ordinary worker cancellation drain expired")

type RuntimeConfig struct {
	rendered                                                                                      bool
	databaseURL, redisURL, source, plan, projection, metricsAddress, dataDirectory                string
	epoch                                                                                         int64
	concurrency                                                                                   int
	leaseTTL, heartbeat, taskTimeout, shutdownGrace, cancellationGrace, idleBackoff, stallTimeout time.Duration
	delay                                                                                         float64
	maxDomains                                                                                    int
	internalHosts                                                                                 []string
	proxy                                                                                         proxyRuntimeConfig
	circuits                                                                                      queue.HostCircuitSettings
}

// InstalledBuildRevision uses executable-owned build metadata, never an environment
// variable supplied as an assertion of the running binary's identity. Docker builds
// without .git must inject the protected immutable release revision at link time.
func InstalledBuildRevision(linked string) (string, error) {
	revision, dirty := "", false
	if info, ok := debug.ReadBuildInfo(); ok {
		for _, setting := range info.Settings {
			switch setting.Key {
			case "vcs.revision":
				revision = setting.Value
			case "vcs.modified":
				dirty = setting.Value != "false"
			}
		}
	}
	if dirty || linked != "" && !sourcePattern.MatchString(linked) || revision != "" && !sourcePattern.MatchString(revision) || linked != "" && revision != "" && linked != revision {
		return "", ErrStartup
	}
	if linked != "" {
		revision = linked
	}
	if !sourcePattern.MatchString(revision) {
		return "", ErrStartup
	}
	return revision, nil
}

// ReadRuntimeConfig freezes protected process inputs. It cannot activate/stage a
// cohort, adopt an allocator epoch or accept board-derived network authority.
func ReadRuntimeConfig(getenv func(string) string, installedRevision string) (RuntimeConfig, error) {
	var c RuntimeConfig
	if getenv == nil || getenv("ORDINARY_GO_WORKER_MODE") != "enabled" || !sourcePattern.MatchString(installedRevision) {
		return c, ErrStartup
	}
	mode := getenv("ORDINARY_GO_RENDERED_DETAILS")
	if mode != "" && mode != "enabled" {
		return RuntimeConfig{}, ErrStartup
	}
	c.rendered = mode == "enabled"
	c.source = getenv("ORDINARY_OWNERSHIP_SOURCE_REVISION")
	c.plan = getenv("ORDINARY_OWNERSHIP_PLAN_SHA256")
	c.projection = getenv("ORDINARY_OWNERSHIP_PROJECTION_SHA1")
	if c.source != installedRevision || !planPattern.MatchString(c.plan) || !sourcePattern.MatchString(c.projection) {
		return RuntimeConfig{}, ErrStartup
	}
	integer := func(key string, fallback, min, max int64) (int64, error) {
		raw := getenv(key)
		if raw == "" {
			return fallback, nil
		}
		value, err := strconv.ParseInt(raw, 10, 64)
		if err != nil || strconv.FormatInt(value, 10) != raw || value < min || value > max {
			return 0, ErrStartup
		}
		return value, nil
	}
	epoch, err := integer("ORDINARY_OWNERSHIP_ROUTING_EPOCH", 0, 1, 9999999999999)
	if err != nil || epoch == 0 {
		return RuntimeConfig{}, ErrStartup
	}
	c.epoch = epoch
	c.databaseURL = getenv("LOCAL_DATABASE_URL")
	c.redisURL = getenv("REDIS_URL")
	if c.databaseURL == "" || c.redisURL == "" {
		return RuntimeConfig{}, ErrStartup
	}
	discovery, err := integer("DISCOVERY_CONCURRENCY", 20, 1, 256)
	if err != nil {
		return RuntimeConfig{}, err
	}
	monitor, err := integer("MONITOR_CONCURRENCY", 5, 0, 256)
	if err != nil {
		return RuntimeConfig{}, err
	}
	if monitor == 0 {
		monitor = discovery
	}
	c.concurrency = int(min(discovery, monitor))
	for _, option := range []struct {
		key                string
		dest               *time.Duration
		fallback, min, max int64
	}{
		{"INFLIGHT_LEASE_TTL_SECONDS", &c.leaseTTL, 600, 30, 86400},
		{"INFLIGHT_HEARTBEAT_INTERVAL_SECONDS", &c.heartbeat, 120, 1, 3600},
		{"ORDINARY_GO_TASK_TIMEOUT_SECONDS", &c.taskTimeout, 600, 1, 86400},
		{"SHUTDOWN_GRACE_SECONDS", &c.shutdownGrace, 30, 0, 45},
		{"ORDINARY_GO_CANCELLATION_GRACE_SECONDS", &c.cancellationGrace, 5, 1, 10},
		{"PIPELINE_STALL_TIMEOUT_SECONDS", &c.stallTimeout, 600, 30, 86400},
	} {
		value, err := integer(option.key, option.fallback, option.min, option.max)
		if err != nil {
			return RuntimeConfig{}, err
		}
		*option.dest = time.Duration(value) * time.Second
	}
	if c.heartbeat+15*time.Second >= c.leaseTTL {
		return RuntimeConfig{}, ErrStartup
	}
	c.idleBackoff = time.Second
	c.maxDomains = 10
	c.delay = 2
	if raw := getenv("THROTTLE_DELAY_DEFAULT"); raw != "" {
		v, err := strconv.ParseFloat(raw, 64)
		if err != nil || math.IsNaN(v) || math.IsInf(v, 0) || v < 0 || v > 3600 {
			return RuntimeConfig{}, ErrStartup
		}
		c.delay = v
	}
	c.circuits = queue.DefaultHostCircuitSettings()
	threshold, err := integer("HOST_CIRCUIT_FAILURE_THRESHOLD", 3, 1, 10000)
	if err != nil {
		return RuntimeConfig{}, err
	}
	c.circuits.FailureThreshold = int(threshold)
	for _, o := range []struct {
		key      string
		dest     *time.Duration
		fallback int64
	}{
		{"HOST_CIRCUIT_FAILURE_WINDOW_SECONDS", &c.circuits.FailureWindow, 600}, {"HOST_CIRCUIT_OPEN_SECONDS", &c.circuits.OpenDuration, 1800}, {"HOST_CIRCUIT_PROBE_SECONDS", &c.circuits.ProbeDuration, 600},
	} {
		v, err := integer(o.key, o.fallback, 1, 86400)
		if err != nil {
			return RuntimeConfig{}, err
		}
		*o.dest = time.Duration(v) * time.Second
	}
	// Keep native internal-service exceptions identical to the protected Python
	// startup derivation. These endpoints are frozen before any queue evidence.
	hosts := []string{}
	var proxyURLs []string
	if raw := getenv("WEBSHARE_PROXY_URLS"); raw != "" {
		if len(raw) > 1<<20 || json.Unmarshal([]byte(raw), &proxyURLs) != nil || len(proxyURLs) > 10000 {
			return RuntimeConfig{}, ErrStartup
		}
	}
	if raw := getenv("WEBSHARE_PROXY_URL"); raw != "" {
		proxyURLs = append(proxyURLs, raw)
	}
	for _, raw := range proxyURLs {
		u, err := url.Parse(raw)
		if err != nil || u.Hostname() == "" {
			return RuntimeConfig{}, ErrStartup
		}
		hosts = append(hosts, u.Hostname())
	}

	for _, key := range []string{"LOCAL_DATABASE_URL", "REDIS_URL", "DATABASE_URL"} {
		if raw := getenv(key); raw != "" {
			u, err := url.Parse(raw)
			if err != nil || u.Hostname() == "" && !(key == "REDIS_URL" && u.Scheme == "unix") {
				return RuntimeConfig{}, ErrStartup
			}
			if u.Hostname() != "" {
				hosts = append(hosts, u.Hostname())
			}
		}
	}
	for _, key := range []string{"TYPESENSE_HOST"} {
		if host := getenv(key); host != "" {
			hosts = append(hosts, host)
		}
	}
	if raw := getenv("INTERNAL_HOSTS_ALLOW"); raw != "" {
		for _, host := range strings.Split(raw, ",") {
			host = strings.TrimSpace(host)
			if host == "" {
				return RuntimeConfig{}, ErrStartup
			}
			hosts = append(hosts, host)
		}
	}
	seen := map[string]bool{}
	for _, host := range hosts {
		if strings.ContainsAny(host, "/@%\r\n\x00") {
			return RuntimeConfig{}, ErrStartup
		}
		// Python startup strips an optional port; raw IPv6 literals stay intact.
		if h, _, err := net.SplitHostPort(host); err == nil {
			host = h
		}
		if strings.HasPrefix(host, "[") && strings.HasSuffix(host, "]") {
			host = host[1 : len(host)-1]
		}
		normalized, err := directHost(host)
		if err != nil || normalized == "" || strings.ContainsAny(normalized, "/@%\r\n\x00") {
			return RuntimeConfig{}, ErrStartup
		}
		if !seen[normalized] {
			seen[normalized] = true
			c.internalHosts = append(c.internalHosts, normalized)
		}
	}
	c.dataDirectory = getenv("ORDINARY_GO_DATA_DIRECTORY")
	if c.dataDirectory == "" {
		c.dataDirectory = "/app/data"
	}
	if !filepath.IsAbs(c.dataDirectory) || strings.ContainsAny(c.dataDirectory, "\r\n\x00") {
		return RuntimeConfig{}, ErrStartup
	}
	c.metricsAddress = getenv("ORDINARY_GO_METRICS_ADDRESS")
	if c.metricsAddress == "" {
		c.metricsAddress = "127.0.0.1:9104"
	}
	host, port, err := net.SplitHostPort(c.metricsAddress)
	p, parseErr := strconv.Atoi(port)
	if err != nil || parseErr != nil || p < 1 || p > 65535 || net.ParseIP(host) == nil {
		return RuntimeConfig{}, ErrStartup
	}
	hash := sha256.Sum256(pinnedCA)
	if hex.EncodeToString(hash[:]) != pinnedCASHA256 {
		return RuntimeConfig{}, ErrStartup
	}
	c.proxy, err = readProxyRuntimeConfig(getenv)
	if err != nil {
		return RuntimeConfig{}, ErrStartup
	}
	return c, nil
}

// BuildIdentity contains only public immutable executable identities.
type BuildIdentity struct {
	SourceRevision string      `json:"source_revision"`
	CASHA256       string      `json:"ca_sha256"`
	Profile        string      `json:"profile"`
	Profiles       [197]string `json:"profiles"`
}

func Identity(linked string) (BuildIdentity, error) {
	revision, err := InstalledBuildRevision(linked)
	if err != nil {
		return BuildIdentity{}, err
	}
	return BuildIdentity{revision, pinnedCASHA256, "greenhouse.token-skip/v1", [197]string{"greenhouse.token-skip/v1", "ashby.token-skip/v1", "lever.token-skip/v1", "recruitee.api-skip/v1", "pinpoint.slug-skip/v1", "rss.teamtailor-skip/v1", "rss.successfactors-skip/v1", "personio.xml-skip/v1", "workday.cxs-urls/v1", "workday.cxs-detail/v1", "jsonld.direct-detail/v1", "smartrecruiters.api-detail/v1", "workable.api-detail/v1", "smartrecruiters.api-urls/v1", "workable.api-urls/v1", "join.nextdata-urls/v1", "join.nextdata-detail/v1", "sitemap.explicit-urls/v1", "dom.direct-detail/v1", "dom.direct-urls/v1", "dom.rendered-urls/v1", "dom.rendered-detail/v1", "jsonld.rendered-detail/v1", "api_sniffer.http-items/v1", "oracle_hcm.finder-items/v1", "oracle_hcm.api-detail/v1", "embedded.direct-detail/v1", "embedded.rendered-detail/v1", "api_sniffer.http-detail/v1", "icims.listing-urls/v1", "breezy.api-urls/v1", "gem.token-skip/v1", "jazzhr.listing-urls/v1", "gupy.nextdata-urls/v1", "phenom.sitemap-urls/v1", "jobylon.embed-items/v1", "nextdata.embedded-items/v1", "nextdata.embedded-urls/v1", "nextdata.rendered-items/v1", "nextdata.rendered-urls/v1", "beisen.portal-items/v1", "inline.document-items/v1", "personio.xml-items/v1", "rss.teamtailor-items/v1", "rss.successfactors-items/v1", "mokahr.encrypted-items/v1", "almacareer.graphql-items/v1", "eightfold.pcsx-sitemap/v1", "mokahr.encrypted-detail/v1", "eightfold.jsonld-api-detail/v1", "softgarden.inline-urls/v1", "ukg.search-items/v1", "bamboohr.careers-list/v1", "recruiter-co-kr.jobflex/v1", "rss.generic-skip/v1", "rss.generic-items/v1", "comeet.hosted-items/v1", "comeet.api-items/v1", "jobvite.listing-urls/v1", "adp.search-items/v1", "cornerstone.search-items/v1", "paylocity.embedded-items/v1", "adp.public-detail/v1", "paylocity.html-detail/v1", "paycom.preview-items/v1", "rippling.v1-urls/v1", "paycom.public-detail/v1", "rippling.v1-detail/v1", "paylocity.proxy-embedded-items/v1", "paylocity.proxy-html-detail/v1", "dayforce.session-search/v1", "dom.proxy-urls/v1", "api_sniffer.proxy-http-items/v1", "inline.proxy-document-items/v1", "sitemap.proxy-explicit-urls/v1", "eightfold.proxy-pcsx-sitemap/v1", "phenom.proxy-sitemap-urls/v1", "eightfold.proxy-jsonld-api-detail/v1", "dom.proxy-detail/v1", "jsonld.proxy-detail/v1", "api_sniffer.proxy-http-detail/v1", "rss.governmentjobs-skip/v1", "rss.governmentjobs-items/v1", "rss.zoho_recruit-skip/v1", "rss.zoho_recruit-items/v1", "dom.direct-rows/v1", "dom.proxy-rows/v1", "dom.rendered-rows/v1", "rss.hr_manager-skip/v1", "rss.hr_manager-items/v1", "rss.successfactors-legacy-xml-skip/v1", "rss.successfactors-legacy-xml-items/v1", "rss.generic-summary-skip/v1", "rss.generic-summary-items/v1", "rss.wp_job_manager-skip/v1", "rss.wp_job_manager-items/v1", "rss.rendered-generic-skip/v1", "rss.rendered-generic-items/v1", "rss.rendered-generic-summary-skip/v1", "rss.rendered-generic-summary-items/v1", "rss.rendered-wp_job_manager-skip/v1", "rss.rendered-wp_job_manager-items/v1", "inline.rendered-items/v1", "manatal.career-items/v1", "hrmos.listing-urls/v1", "recruiterbox.listing-urls/v1", "jobs_ch.company-urls/v1", "api_sniffer.browser-items/v1", "deel.public-items/v1", "hibob.public-items/v1", "traffit.public-items/v1", "earcu.public-items/v1", "earcu.proxy-feed-items/v1", "cvwarehouse.public-items/v1", "woowa.public-items/v1", "beehire.public-items/v1", "hirehive.public-items/v1", "welcometothejungle.public-items/v1", "computrabajo.listing-urls/v1", "computrabajo.proxy-listing-urls/v1", "ycombinator.listing-urls/v1", "talentbrew.listing-urls/v1", "intervieweb.form-listing-urls/v1", "typify.partition-items/v1", "universia.public-items/v1", "talentreef.brand-items/v1", "umantis.listing-urls/v1", "umantis.proxy-listing-urls/v1", "notion.public-urls/v1", "notion.public-detail/v1", "unifr.authoritative-items/v1", "pdf.public-detail/v1", "seek.advertiser-urls/v1", "avature.listing-urls/v1", "seek.graphql-detail/v1", "linkedin.guest-items/v1", "taleo.listing-urls/v1", "practicematch.proxy-listing-urls/v1", "linkedin.guest-detail/v1", "jazzhr.public-detail/v1", "taleo.enterprise-detail/v1", "jobbank104.public-items/v1", "jobbank104.proxy-public-items/v1", "cnstaff.public-items/v1", "seamlesshiring.public-items/v1", "jarvi.public-items/v1", "job51.public-items/v1", "paynet.public-items/v1", "nowhiring.public-items/v1", "fenbi.public-items/v1", "wecruit.public-items/v1", "ashby.token-items/v1", "lever.token-items/v1", "recruitee.api-items/v1", "pageup.listing-items/v1", "infoniqa.session-urls/v1", "keka.public-items/v1", "turbohire.public-items/v1", "curately.public-items/v1", "inploi.public-items/v1", "jobconvo.listing-urls/v1", "jobconvo.public-detail/v1", "workable.proxy-api-urls/v1", "workable.proxy-api-detail/v1", "smartrecruiters.canonical-items/v1", "jobstreet.company-items/v1", "jobstreet.graphql-detail/v1", "rss.successfactors-legacy-session-items/v1", "darwinbox.session-items/v1", "bytedance.partition-items/v1", "accenture.http-items/v1", "candidatus.http-postback-urls/v1", "johdi.listing-urls/v1", "jobdiva.listing-urls/v1", "headhunter.summary-items/v1", "headhunter.proxy-summary-items/v1", "johdi.api-detail/v1", "headhunter.api-detail/v1", "headhunter.proxy-api-detail/v1", "papa_johns.listing-urls/v1", "papa_johns.proxy-listing-urls/v1", "infor.session-items/v1", "peoplesoft.session-items/v1", "unisante.authoritative-items/v1", "infor.session-detail/v1", "peoplesoft.session-detail/v1", "talemetry.listing-urls/v1", "talemetry.proxy-listing-urls/v1", "talemetry.json-urls/v1", "talemetry.proxy-json-urls/v1", "prospective.localized-items/v1", "kipt.bulletin-items/v1", "rss.successfactors-rmk-skip/v1", "rss.successfactors-rmk-items/v1", "rss.successfactors-rmk-proxy-skip/v1", "rss.successfactors-rmk-proxy-items/v1", "amazon.partitioned-items/v1"}}, nil
}
