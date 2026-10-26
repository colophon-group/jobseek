package apisniffer

import "testing"

func TestJob51CampusAboutBoardKeepsEmployerAndURLBounds(t *testing.T) {
	const source = "https://campus.51job.com/rrd2027/about1.html"
	origin, err := Job51BoardOrigin(source)
	if err != nil || origin != "https://campus.51job.com" {
		t.Fatal(origin, err)
	}
	for _, bad := range []string{source + "?jobarea=020000", source + "#jobs", "https://pvh.51job.com/rrd2027/about1.html", "https://campus.51job.com/rrd2027/about.html", "http://campus.51job.com/rrd2027/about1.html", "https://campus.51job.com.evil.test/rrd2027/about1.html"} {
		if _, err := Job51BoardOrigin(bad); err == nil {
			t.Fatal("unbound board admitted", bad)
		}
	}
}
