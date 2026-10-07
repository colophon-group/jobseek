// Package-level URL resolution preserves the existing urllib byte identity.
package apisniffer

import (
	"errors"
	"golang.org/x/text/unicode/norm"
	"net"
	"regexp"
	"strings"
)

type PythonURL struct{ Scheme, Host, Path, Params, Query, Fragment string }

func PythonJoinURL(base, reference string) (string, error) {
	if base == "" {
		return reference, nil
	}
	if reference == "" {
		return base, nil
	}
	b, ok := ParsePythonURL(base)
	if !ok {
		return "", errors.New("invalid base URL")
	}
	p, ok := ParsePythonURL(reference)
	if !ok {
		return "", errors.New("invalid reference URL")
	}
	if p.Scheme == "" {
		p.Scheme = b.Scheme
	}
	if p.Scheme != b.Scheme || p.Scheme != "http" && p.Scheme != "https" {
		return reference, nil
	}
	if p.Host != "" {
		return p.String(), nil
	}
	p.Host = b.Host
	if p.Path == "" && p.Params == "" {
		p.Path, p.Params = b.Path, b.Params
		if p.Query == "" {
			p.Query = b.Query
		}
		return p.String(), nil
	}
	segments := strings.Split(p.Path, "/")
	if !strings.HasPrefix(p.Path, "/") {
		parts := strings.Split(b.Path, "/")
		if parts[len(parts)-1] != "" {
			parts = parts[:len(parts)-1]
		}
		segments = append(parts, segments...)
		filtered := []string{segments[0]}
		if len(segments) > 2 {
			for _, segment := range segments[1 : len(segments)-1] {
				if segment != "" {
					filtered = append(filtered, segment)
				}
			}
		}
		if len(segments) > 1 {
			filtered = append(filtered, segments[len(segments)-1])
		}
		segments = filtered
	}
	resolved := []string{}
	for _, segment := range segments {
		switch segment {
		case "..":
			if len(resolved) > 0 {
				resolved = resolved[:len(resolved)-1]
			}
		case ".":
		default:
			resolved = append(resolved, segment)
		}
	}
	if last := segments[len(segments)-1]; last == "." || last == ".." {
		resolved = append(resolved, "")
	}
	p.Path = strings.Join(resolved, "/")
	if p.Path == "" {
		p.Path = "/"
	}
	return p.String(), nil
}

var urlScheme = regexp.MustCompile(`^[A-Za-z][A-Za-z0-9+.-]*$`)
var ipvFuture = regexp.MustCompile(`^v[0-9A-Fa-f]+\..+$`)
var paramSchemes = map[string]bool{"": true, "ftp": true, "hdl": true, "prospero": true, "http": true, "imap": true, "https": true, "shttp": true, "rtsp": true, "rtsps": true, "rtspu": true, "sip": true, "sips": true, "mms": true, "sftp": true, "tel": true}

func ParsePythonURL(raw string) (PythonURL, bool) {
	s := strings.TrimLeftFunc(raw, func(r rune) bool { return r <= 32 })
	s = strings.NewReplacer("\t", "", "\r", "", "\n", "").Replace(s)
	p := PythonURL{}
	if colon := strings.IndexByte(s, ':'); colon >= 0 && urlScheme.MatchString(s[:colon]) {
		p.Scheme = strings.ToLower(s[:colon])
		s = s[colon+1:]
	}
	if strings.HasPrefix(s, "//") {
		s = s[2:]
		end := strings.IndexAny(s, "/?#")
		if end < 0 {
			end = len(s)
		}
		p.Host, s = s[:end], s[end:]
		if strings.ContainsAny(p.Host, "[]") {
			host := p.Host
			if at := strings.LastIndexByte(host, '@'); at >= 0 {
				host = host[at+1:]
			}
			open, close := strings.IndexByte(host, '['), strings.IndexByte(host, ']')
			if open != 0 || close < open || (close+1 < len(host) && host[close+1] != ':') {
				return PythonURL{}, false
			}
			bracket := host[open+1 : close]
			if !ipvFuture.MatchString(bracket) {
				address := bracket
				if scope := strings.IndexByte(address, '%'); scope >= 0 {
					address = address[:scope]
				}
				if ip := net.ParseIP(address); ip == nil || !strings.Contains(address, ":") {
					return PythonURL{}, false
				}
			}
		}
		checked := strings.NewReplacer("@", "", ":", "", "#", "", "?", "").Replace(p.Host)
		if normalized := norm.NFKC.String(checked); normalized != checked && strings.ContainsAny(normalized, "/?#@:") {
			return PythonURL{}, false
		}
	}
	if fragment := strings.IndexByte(s, '#'); fragment >= 0 {
		p.Fragment, s = s[fragment+1:], s[:fragment]
	}
	if query := strings.IndexByte(s, '?'); query >= 0 {
		p.Query, s = s[query+1:], s[:query]
	}
	p.Path = s
	if paramSchemes[p.Scheme] {
		start := strings.LastIndexByte(s, '/') + 1
		if semi := strings.IndexByte(s[start:], ';'); semi >= 0 {
			index := start + semi
			p.Path, p.Params = s[:index], s[index+1:]
		}
	}
	return p, true
}

func (p PythonURL) String() string {
	path := p.Path
	if p.Params != "" {
		path += ";" + p.Params
	}
	result := ""
	if p.Scheme != "" {
		result = p.Scheme + ":"
	}
	if p.Host != "" {
		result += "//" + p.Host
		if path != "" && !strings.HasPrefix(path, "/") {
			result += "/"
		}
	}
	result += path
	if p.Query != "" {
		result += "?" + p.Query
	}
	if p.Fragment != "" {
		result += "#" + p.Fragment
	}
	return result
}
