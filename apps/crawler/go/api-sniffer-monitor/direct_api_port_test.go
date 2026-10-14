package apisniffer

import "testing"

func TestDirectPublisherAPIPortRequiresOriginalBoardAndMethod(t *testing.T) {
	metadata := `{"api_url":"https://cdncareers.didiglobal.com:34003/icims/searchJobList","method":"POST","json_path":"result","url_template":"https://careers.didiglobal.com/jobDetail:{id}","fields":{"title":"jobTitle"}}`
	if _, err := OptionsFromMetadata("https://careers.didiglobal.com", metadata); err != nil {
		t.Fatal("original publisher API port refused", err)
	}
	if _, err := OptionsFromMetadata("https://other.invalid", metadata); err == nil {
		t.Fatal("publisher port exception detached from board")
	}
	for _, source := range []string{
		"https://cdncareers.didiglobal.com:34004/icims/searchJobList",
		"https://cdncareers.didiglobal.com:34003/other",
		"https://cdncareers.didiglobal.com:34003/icims/searchJobList?extra=1",
		"https://other.invalid:34003/icims/searchJobList",
	} {
		if validURL(source) {
			t.Fatal("unqualified port endpoint accepted")
		}
	}
	if _, err := OptionsFromMetadata("https://careers.didiglobal.com", `{"api_url":"https://cdncareers.didiglobal.com:34003/icims/searchJobList","method":"GET","json_path":"result"}`); err == nil {
		t.Fatal("unqualified API method accepted")
	}
}
