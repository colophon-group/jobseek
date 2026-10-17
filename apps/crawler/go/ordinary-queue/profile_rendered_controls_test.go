package queue

import "testing"

func TestQualifiedRenderedControlSourcesRemainScoped(t *testing.T) {
	for digest, host := range renderedDOMControlSources {
		for _, source := range []string{"https://" + host + "/fixture-job", "https://" + host + ":443/fixture-job"} {
			if !qualifiedRenderedControlSource(digest, source) {
				t.Fatal("bound verified-TLS source rejected")
			}
		}
		for _, source := range []string{
			"http://" + host + "/fixture-job",
			"https://" + host + ":8443/fixture-job",
			"https://user@" + host + "/fixture-job",
			"https://" + host + ".foreign.example/fixture-job",
			"https://foreign.example/fixture-job",
			"https://127.0.0.1/fixture-job",
		} {
			if qualifiedRenderedControlSource(digest, source) {
				t.Fatal("foreign transport or destination accepted")
			}
		}
		if qualifiedRenderedControlSource("unbound", "https://"+host+"/fixture-job") {
			t.Fatal("unqualified configuration accepted")
		}
	}
}
