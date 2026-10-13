package documentactions

import "testing"

func TestInlineCardsRequiresBoundedCompleteIdentityBinding(t *testing.T) {
	c := InlineCards{ControlSelector: "a.card", ContentSelector: "div.detail", IdentitySelector: "p.provider", IdentityAttribute: "id", IdentityPattern: `([A-Za-z0-9]+)-[0-9]+`, Maximum: 500, TimeoutMS: 300000}
	if !c.Valid() {
		t.Fatal("valid card contract refused")
	}
	if id, ok := c.Identity("provider123-47"); !ok || id != "provider123" {
		t.Fatal("provider identity differs")
	}
	for _, raw := range []string{"provider123", "-47", "provider123-47-tail"} {
		if _, ok := c.Identity(raw); ok {
			t.Fatal("partial provider identity accepted")
		}
	}
	for _, mutate := range []func(*InlineCards){func(c *InlineCards) { c.Maximum = 501 }, func(c *InlineCards) { c.TimeoutMS = 300001 }, func(c *InlineCards) { c.IdentityPattern = "(a)(b)" }, func(c *InlineCards) { c.ControlSelector = "a >> iframe" }, func(c *InlineCards) { c.IdentityAttribute = "bad attribute" }} {
		bad := c
		mutate(&bad)
		if bad.Valid() {
			t.Fatal("unbounded/unsupported card binding accepted")
		}
	}
}

func TestInlineCardWireRejectsDuplicateIdentityAndUnboundEnvelope(t *testing.T) {
	r := Response{Protocol: Protocol, RequestID: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", ConfigFingerprint: "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", Result: []byte{1}, InlineIdentities: []string{"provider", "provider"}}
	if r.Valid() {
		t.Fatal("duplicate identity gained response authority")
	}
	r.InlineIdentities = []string{"provider"}
	if !r.Valid() {
		t.Fatal("bounded unique identity refused")
	}
}
