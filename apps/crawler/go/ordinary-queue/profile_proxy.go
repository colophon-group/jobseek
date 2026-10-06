package queue

// Transport choice is compiled into immutable profile identities; neither a
// generic caller flag nor a runtime selector grants proxy write authority.
func ProfileRequiresProxy(profile string) bool {
	return profile == "paylocity.proxy-embedded-items/v1" || profile == paylocityProxyDetailProfile
}

func (a *Authority) RequiresProxyHTTP() bool {
	if a == nil || a.ownership == nil {
		return false
	}
	for _, m := range a.ownership.document.Members {
		if ProfileRequiresProxy(m.Profile) {
			return true
		}
	}
	for _, d := range a.ownership.document.Details {
		if ProfileRequiresProxy(d.Profile) {
			return true
		}
	}
	return false
}
