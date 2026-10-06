package worker

import (
	"container/list"
	"errors"
	"sync"
	"time"
)

var errProxyPoolExhausted = errors.New("native proxy pool exhausted")

type proxyHealth struct {
	failures      int
	until         float64
	due, inFlight bool
	generation    int
}
type proxyOrigin struct {
	slot   int
	origin string
}
type proxyOriginState struct {
	key    proxyOrigin
	health proxyHealth
}
type proxySelection struct {
	pool                               *proxyPool
	slot                               int
	origin                             string
	globalGeneration                   int
	originGeneration                   *int
	globalProbe, originProbe, halfOpen bool
}

func (proxySelection) String() string   { return "native proxy selection" }
func (proxySelection) GoString() string { return "native proxy selection" }

// This is the process-local Webshare policy already used by the legacy HTTP
// client: origin circuits, globally unhealthy exits and generation-owned probes.
// Endpoints/credentials belong to its sealed transport, never health keys or logs.
type proxyPool struct {
	mu                   sync.Mutex
	cursor, size, forced int
	global               []proxyHealth
	origins              map[proxyOrigin]*list.Element
	lru                  *list.List
	evidence             []map[string]float64
	now                  func() float64
}

func newProxyPool(size, forced int, now func() float64) (*proxyPool, error) {
	if size < 1 || size > 64 || forced < -1 || forced >= size || now == nil {
		return nil, ErrStartup
	}
	p := &proxyPool{size: size, forced: forced, global: make([]proxyHealth, size), origins: map[proxyOrigin]*list.Element{}, lru: list.New(), evidence: make([]map[string]float64, size), now: now}
	for i := range p.evidence {
		p.evidence[i] = map[string]float64{}
	}
	return p, nil
}
func newLiveProxyPool(size, forced int) (*proxyPool, error) {
	started := time.Now()
	return newProxyPool(size, forced, func() float64 { return time.Since(started).Seconds() })
}
func (p *proxyPool) origin(slot int, origin string, create bool) *proxyHealth {
	if origin == "" {
		return nil
	}
	key := proxyOrigin{slot, origin}
	if entry := p.origins[key]; entry != nil {
		p.lru.MoveToBack(entry)
		return &entry.Value.(*proxyOriginState).health
	}
	if !create {
		return nil
	}
	if len(p.origins) >= 10000 {
		first := p.lru.Front()
		delete(p.origins, first.Value.(*proxyOriginState).key)
		p.lru.Remove(first)
	}
	state := &proxyOriginState{key: key}
	p.origins[key] = p.lru.PushBack(state)
	return &state.health
}
func proxyEligible(h *proxyHealth, now float64) bool {
	return h == nil || h.until <= now && !h.inFlight
}
func proxyRecover(h *proxyHealth, generation *int, owns bool) bool {
	if h == nil || !owns || generation == nil || *generation != h.generation || h.failures == 0 || !h.inFlight {
		return false
	}
	h.failures, h.until, h.due, h.inFlight = 0, 0, false, false
	h.generation++
	return true
}
func proxyRelease(h *proxyHealth, generation *int, owns bool) {
	if h != nil && owns && generation != nil && *generation == h.generation && h.inFlight {
		h.inFlight, h.due = false, true
	}
}
func (p *proxyPool) selectEndpoint(origin string) (*proxySelection, error) {
	p.mu.Lock()
	defer p.mu.Unlock()
	now := p.now()
	eligible, recovery := []int{}, []int{}
	ordered := []int{}
	if p.forced >= 0 {
		ordered = append(ordered, p.forced)
	} else {
		for i := 0; i < p.size; i++ {
			ordered = append(ordered, (p.cursor+i)%p.size)
		}
	}
	for _, slot := range ordered {
		g, o := &p.global[slot], p.origin(slot, origin, false)
		if !proxyEligible(g, now) || !proxyEligible(o, now) {
			continue
		}
		eligible = append(eligible, slot)
		if g.due || o != nil && o.due {
			recovery = append(recovery, slot)
		}
	}
	if len(eligible) == 0 {
		return nil, errProxyPoolExhausted
	}
	slot := eligible[0]
	selection := &proxySelection{pool: p, origin: origin}
	if len(recovery) > 0 {
		slot = recovery[0]
		g, o := &p.global[slot], p.origin(slot, origin, false)
		g.due, g.inFlight = false, g.failures > 0
		if o != nil {
			o.due, o.inFlight = false, o.failures > 0
		}
		selection.globalProbe = g.inFlight
		selection.originProbe = o != nil && o.inFlight
		selection.halfOpen = selection.globalProbe || selection.originProbe
	} else {
		minimum := int(^uint(0) >> 1)
		for _, candidate := range eligible {
			o := p.origin(candidate, origin, false)
			penalty := p.global[candidate].failures
			if o != nil {
				penalty += o.failures
			}
			if penalty < minimum {
				minimum, slot = penalty, candidate
			}
		}
	}
	p.cursor = (slot + 1) % p.size
	selection.slot = slot
	selection.globalGeneration = p.global[slot].generation
	if o := p.origin(slot, origin, false); o != nil {
		gen := o.generation
		selection.originGeneration = &gen
	}
	return selection, nil
}
func (p *proxyPool) success(s *proxySelection) {
	p.mu.Lock()
	defer p.mu.Unlock()
	if s == nil || s.pool != p || s.slot < 0 || s.slot >= p.size {
		return
	}
	g := &p.global[s.slot]
	if s.globalGeneration == g.generation {
		clear(p.evidence[s.slot])
	}
	proxyRecover(g, &s.globalGeneration, s.globalProbe)
	proxyRecover(p.origin(s.slot, s.origin, false), s.originGeneration, s.originProbe)
}
func (p *proxyPool) abandon(s *proxySelection) {
	p.mu.Lock()
	defer p.mu.Unlock()
	if s == nil || s.pool != p || s.slot < 0 || s.slot >= p.size {
		return
	}
	proxyRelease(&p.global[s.slot], &s.globalGeneration, s.globalProbe)
	proxyRelease(p.origin(s.slot, s.origin, false), s.originGeneration, s.originProbe)
}
func (p *proxyPool) failure(s *proxySelection, origin, reason string) {
	p.mu.Lock()
	defer p.mu.Unlock()
	if s == nil || s.pool != p || s.slot < 0 || s.slot >= p.size {
		return
	}
	if reason != "proxy_auth" && reason != "proxy_transport" && reason != "origin_block" && reason != "origin_transport" {
		return
	}
	now := p.now()
	global := &p.global[s.slot]
	selectedOrigin := p.origin(s.slot, s.origin, false)
	if s.globalGeneration != global.generation {
		proxyRelease(selectedOrigin, s.originGeneration, s.originProbe)
		return
	}
	existing := p.origin(s.slot, origin, false)
	originScope := origin != "" && (reason == "origin_block" || reason == "origin_transport")
	if originScope {
		if origin != s.origin {
			proxyRecover(selectedOrigin, s.originGeneration, s.originProbe)
			if existing != nil {
				proxyRecover(global, &s.globalGeneration, s.globalProbe)
				return
			}
		} else if existing == nil {
			if reason == "origin_block" {
				proxyRecover(global, &s.globalGeneration, s.globalProbe)
			} else {
				proxyRelease(global, &s.globalGeneration, s.globalProbe)
			}
			if s.originGeneration != nil {
				return
			}
		} else if s.originGeneration == nil || *s.originGeneration != existing.generation {
			if reason == "origin_block" {
				proxyRecover(global, &s.globalGeneration, s.globalProbe)
			} else {
				proxyRelease(global, &s.globalGeneration, s.globalProbe)
			}
			return
		}
	}
	cooldownReason := reason
	if reason == "origin_transport" && origin != "" {
		evidence := p.evidence[s.slot]
		for host, at := range evidence {
			if at < now-300 {
				delete(evidence, host)
			}
		}
		evidence[origin] = now
		if len(evidence) >= 3 {
			originScope = false
			cooldownReason = "proxy_transport"
			clear(evidence)
		}
	}
	var health *proxyHealth
	if originScope {
		if reason == "origin_block" || origin != s.origin {
			proxyRecover(global, &s.globalGeneration, s.globalProbe)
		} else {
			proxyRelease(global, &s.globalGeneration, s.globalProbe)
		}
		health = p.origin(s.slot, origin, true)
	} else {
		if selectedOrigin != nil && selectedOrigin.failures > 0 {
			proxyRelease(selectedOrigin, s.originGeneration, s.originProbe)
		}
		health = global
	}
	health.failures++
	base, maximum := 120.0, 3600.0
	if cooldownReason == "proxy_auth" {
		base, maximum = 3600, 86400
	} else if cooldownReason == "origin_block" {
		base, maximum = 900, 21600
	}
	cooldown := base
	for i := 1; i < health.failures && cooldown < maximum; i++ {
		cooldown = min(maximum, cooldown*2)
	}
	health.until, health.due, health.inFlight = now+cooldown, true, false
	health.generation++
}
