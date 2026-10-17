package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"net"
	"net/netip"
	"net/url"
	"os"
	"sort"
	"strconv"
	"strings"
	"syscall"
	"time"
)

type hostEndpoint struct {
	Label string `json:"label"`
	Host  string `json:"host"`
	Port  int    `json:"port"`
}
type hostEndpoints struct {
	Required []hostEndpoint `json:"required"`
	Blocked  []hostEndpoint `json:"blocked"`
}

func privateHostIPv4(address netip.Addr) bool {
	return address.Is4() && (netip.MustParsePrefix("10.0.0.0/8").Contains(address) || netip.MustParsePrefix("172.16.0.0/12").Contains(address) || netip.MustParsePrefix("192.168.0.0/16").Contains(address))
}
func buildHostEndpoints(ctx context.Context, envFile, gateway string, lookup func(context.Context, string, string) ([]net.IP, error)) (hostEndpoints, error) {
	empty := hostEndpoints{}
	ip, e := netip.ParseAddr(gateway)
	if e != nil || !privateHostIPv4(ip) {
		return empty, errors.New("bridge gateway must be private IPv4")
	}
	raw, e := exactHostValue(envFile, "LOCAL_DATABASE_URL")
	if e != nil {
		return empty, e
	}
	raw = strings.TrimSpace(raw)
	if len(raw) >= 2 && raw[0] == raw[len(raw)-1] && (raw[0] == '\'' || raw[0] == '"') {
		raw = raw[1 : len(raw)-1]
	}
	u, e := url.Parse(raw)
	if e != nil || (u.Scheme != "postgres" && u.Scheme != "postgresql") || u.Hostname() == "" {
		return empty, errors.New("invalid PostgreSQL URL")
	}
	port := 5432
	if u.Port() != "" {
		port, e = strconv.Atoi(u.Port())
		if e != nil || port < 1 || port > 65535 {
			return empty, errors.New("invalid PostgreSQL port")
		}
	}
	addresses, e := lookup(ctx, "ip4", u.Hostname())
	if e != nil {
		return empty, errors.New("PostgreSQL IPv4 lookup failed")
	}
	unique := map[netip.Addr]bool{}
	for _, address := range addresses {
		ip, ok := netip.AddrFromSlice(address.To4())
		if !ok || !privateHostIPv4(ip) {
			return empty, errors.New("PostgreSQL must resolve only to private IPv4")
		}
		unique[ip] = true
	}
	if len(unique) == 0 {
		return empty, errors.New("PostgreSQL IPv4 lookup empty")
	}
	ordered := make([]netip.Addr, 0, len(unique))
	for ip := range unique {
		ordered = append(ordered, ip)
	}
	sort.Slice(ordered, func(i, j int) bool { return ordered[i].Less(ordered[j]) })
	out := hostEndpoints{Required: []hostEndpoint{{"github-api", "api.github.com", 443}, {"inventory-source", "storage.stapply.ai", 443}}, Blocked: []hostEndpoint{{"crawler-host", gateway, 22}}}
	for i, ip := range ordered {
		out.Blocked = append(out.Blocked, hostEndpoint{fmt.Sprintf("production-postgresql-%d", i+1), ip.String(), port})
	}
	return out, nil
}
func verifyHostEndpoints(ctx context.Context, payload hostEndpoints, connect func(context.Context, string, int) error) (map[string]any, error) {
	if payload.Required == nil || payload.Blocked == nil || len(payload.Blocked) == 0 {
		return nil, errors.New("invalid network endpoint document")
	}
	required, blocked := []string{}, []string{}
	valid := func(endpoint hostEndpoint) bool {
		return endpoint.Label != "" && endpoint.Host != "" && endpoint.Port >= 1 && endpoint.Port <= 65535
	}
	for _, endpoint := range payload.Required {
		if !valid(endpoint) {
			return nil, errors.New("invalid required endpoint")
		}
		if connect(ctx, endpoint.Host, endpoint.Port) != nil {
			return nil, errors.New("required network endpoint unreachable")
		}
		required = append(required, endpoint.Label)
	}
	for _, endpoint := range payload.Blocked {
		if !valid(endpoint) {
			return nil, errors.New("invalid blocked endpoint")
		}
		if connect(ctx, endpoint.Host, endpoint.Port) == nil {
			return nil, errors.New("blocked network endpoint reachable")
		}
		blocked = append(blocked, endpoint.Label)
	}
	return map[string]any{"event": "ats_inventory.network_boundary_verified", "required": required, "blocked": blocked}, nil
}
func runHostNetwork(ctx context.Context, args []string, output io.Writer) error {
	if len(args) < 1 {
		return errors.New("network command required")
	}
	if args[0] == "build" {
		f := flag.NewFlagSet("host-network-probe build", flag.ContinueOnError)
		f.SetOutput(io.Discard)
		env := f.String("env-file", "", "")
		gateway := f.String("gateway", "", "")
		out := f.String("output", "", "")
		if e := f.Parse(args[1:]); e != nil {
			return e
		}
		if *env == "" || *gateway == "" || *out == "" || f.NArg() != 0 {
			return errors.New("invalid network build arguments")
		}
		payload, e := buildHostEndpoints(ctx, *env, *gateway, net.DefaultResolver.LookupIP)
		if e != nil {
			return e
		}
		raw, e := json.Marshal(payload)
		if e != nil {
			return e
		}
		fd, e := syscall.Open(*out, syscall.O_WRONLY|syscall.O_TRUNC|syscall.O_NOFOLLOW, 0)
		if e != nil {
			return errors.New("network endpoint output unavailable")
		}
		file := os.NewFile(uintptr(fd), *out)
		defer file.Close()
		if e = file.Chmod(0600); e == nil {
			_, e = file.Write(append(raw, '\n'))
		}
		if e == nil {
			e = file.Sync()
		}
		return e
	}
	if args[0] != "verify" || len(args) != 2 {
		return errors.New("invalid network verify arguments")
	}
	raw, e := boundedInventoryFile(args[1], 1024*1024)
	if e != nil {
		return errors.New("network endpoint document unavailable")
	}
	var payload hostEndpoints
	if json.Unmarshal(raw, &payload) != nil {
		return errors.New("invalid endpoint JSON")
	}
	connect := func(ctx context.Context, host string, port int) error {
		dialer := net.Dialer{Timeout: 5 * time.Second}
		c, e := dialer.DialContext(ctx, "tcp", net.JoinHostPort(host, strconv.Itoa(port)))
		if e != nil {
			return e
		}
		return c.Close()
	}
	result, e := verifyHostEndpoints(ctx, payload, connect)
	if e != nil {
		return e
	}
	return json.NewEncoder(output).Encode(result)
}
