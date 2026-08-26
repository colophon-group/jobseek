// Code generated from privacy_registry.json by tools/generate_privacy.py. DO NOT EDIT.

package conformance

var emailNames = map[string]bool{
	"e-mail": true,
	"email":  true,
	"mail":   true,
}

var secretNames = map[string]bool{
	"access-key":    true,
	"access-token":  true,
	"access_key":    true,
	"access_token":  true,
	"accesskey":     true,
	"accesstoken":   true,
	"api-key":       true,
	"api_key":       true,
	"apikey":        true,
	"authorization": true,
	"client-secret": true,
	"client_secret": true,
	"clientsecret":  true,
	"cookie":        true,
	"passphrase":    true,
	"passwd":        true,
	"password":      true,
	"secret":        true,
	"token":         true,
	"x-api-key":     true,
	"x_api_key":     true,
}

var sensitiveHeaders = map[string]bool{
	"api-key":             true,
	"authorization":       true,
	"client-secret":       true,
	"cookie":              true,
	"proxy-authorization": true,
	"set-cookie":          true,
	"x-api-key":           true,
	"x-auth-token":        true,
	"x-client-secret":     true,
}
