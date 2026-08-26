// Code generated from privacy_registry.json by tools/generate_privacy.py. DO NOT EDIT.

package conformance

var credentialSuffixes = []string{"-password", "-secret", "-token", "_password", "_secret", "_token"}

var emailNames = map[string]bool{
	"e-mail": true,
	"email":  true,
	"mail":   true,
}

var secretNames = map[string]bool{
	"access-key":          true,
	"access-token":        true,
	"access_key":          true,
	"access_token":        true,
	"accesskey":           true,
	"accesstoken":         true,
	"api-key":             true,
	"api-token":           true,
	"api_key":             true,
	"api_token":           true,
	"apikey":              true,
	"apitoken":            true,
	"auth":                true,
	"authentication":      true,
	"authorization":       true,
	"client-secret":       true,
	"client_secret":       true,
	"clientsecret":        true,
	"cookie":              true,
	"credential":          true,
	"key":                 true,
	"passphrase":          true,
	"passwd":              true,
	"password":            true,
	"proxy-authorization": true,
	"refresh-token":       true,
	"refresh_token":       true,
	"refreshtoken":        true,
	"secret":              true,
	"session":             true,
	"sessionid":           true,
	"set-cookie":          true,
	"sig":                 true,
	"signature":           true,
	"token":               true,
	"x-api-key":           true,
	"x-api-token":         true,
	"x-client-secret":     true,
	"x-secret":            true,
	"x_api_key":           true,
	"x_api_token":         true,
	"x_client_secret":     true,
	"x_secret":            true,
}

var sensitiveHeaders = map[string]bool{
	"api-key":             true,
	"api-token":           true,
	"authentication":      true,
	"authorization":       true,
	"client-secret":       true,
	"cookie":              true,
	"proxy-authorization": true,
	"set-cookie":          true,
	"x-api-key":           true,
	"x-api-token":         true,
	"x-auth-token":        true,
	"x-client-secret":     true,
	"x-secret":            true,
}
