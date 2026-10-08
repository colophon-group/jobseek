package apisniffer

import "testing"

func TestUmantisTenantListingAndFilterAuthority(t *testing.T) {
	o, err := UmantisOptionsFromMetadata("https://recruitingapp-3040.umantis.com/Jobs/3", `{"customer_id":"3040","listing_path":"/Jobs/3?lang=fre&CompanyID=32&Reset=G&DesignID=10012","strict_listing_contract":true,"expected_employer":"Université de Neuchâtel","employer_field_id":"column_value_1184173","empty_state_text":"Aucune entrée n’a été trouvée."}`)
	if err != nil || !o.Strict {
		t.Fatal(o, err)
	}
	p, err := o.PaginationURL("1184173", 2)
	if err != nil || p != "https://recruitingapp-3040.umantis.com/Jobs/3?lang=fre&CompanyID=32&DesignID=10012&tc1184173=p2" {
		t.Fatal(p, err)
	}
	if !o.ResourceMatches(p) || o.ResourceMatches("https://foreign.umantis.com/Jobs/3") || o.ResourceMatches(o.Origin+"/admin") {
		t.Fatal("tenant scope escaped")
	}
	for _, raw := range []string{`{"customer_id":"3040","listing_path":"//foreign.example/Jobs/All"}`, `{"customer_id":"3040","strict_listing_contract":true}`, `{"customer_id":"3040","strict_listing_contract":1}`, `{"customer_id":"3040","cname":"evil.example"}`} {
		if _, err := UmantisOptionsFromMetadata(o.BoardURL, raw); err == nil {
			t.Fatal("invalid option accepted", raw)
		}
	}
}
