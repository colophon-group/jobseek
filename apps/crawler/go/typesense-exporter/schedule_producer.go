package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"math/big"
	"net"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"syscall"
	"time"

	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/b0task"
	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/framing"
)

const capacityProducerSocket = "/run/jobseek-lightpanda-producer/control.sock"
const capacityProducerProtocol = "jobseek.lightpanda.producer/v1"

var capacityDigest = regexp.MustCompile(`^[0-9a-f]{64}$`)

type capacityProducer struct {
	mode, socket string
	uid          uint32
}

type capacityProducerDecision struct {
	Version               string   `json:"version"`
	Outcome               string   `json:"outcome"`
	Reason                string   `json:"reason"`
	PreparationDigest     string   `json:"preparation_digest"`
	PayloadSHA256         string   `json:"payload_sha256"`
	ExistingState         string   `json:"existing_state"`
	ExistingPayloadSHA256 string   `json:"existing_payload_sha256"`
	Activated             bool     `json:"activated"`
	Cohort                string   `json:"cohort"`
	BoardSlugs            []string `json:"board_slugs"`
	LifetimeOccupancy     int64    `json:"lifetime_occupancy"`
	LifetimeCapacity      int64    `json:"lifetime_capacity"`
	LifetimeHeadroom      int64    `json:"lifetime_headroom"`
}

func capacityProducerDecode(payload []byte) (bool, bool, error) {
	var d capacityProducerDecision
	if json.Unmarshal(payload, &d) != nil {
		return false, false, errors.New("invalid producer response")
	}
	canonical, err := b0task.CanonicalJSON(d, true)
	if err != nil || !bytes.Equal(canonical, payload) || d.Version != capacityProducerProtocol || d.BoardSlugs == nil || d.Cohort != "" || len(d.BoardSlugs) != 0 || d.LifetimeCapacity != 2048 || d.LifetimeOccupancy < 0 || d.LifetimeOccupancy > 2048 || d.LifetimeHeadroom != 2048-d.LifetimeOccupancy {
		return false, false, errors.New("producer response identity differs")
	}
	if d.Outcome == "legacy" && d.Reason == "literal_legacy" && !d.Activated && d.PreparationDigest == "" && d.PayloadSHA256 == "" && d.ExistingState == "" && d.ExistingPayloadSHA256 == "" {
		return false, false, nil
	}
	validState := d.ExistingState == "" && d.ExistingPayloadSHA256 == "" || (d.ExistingState == "ready" || d.ExistingState == "inflight" || d.ExistingState == "dead" || d.ExistingState == "terminal") && capacityDigest.MatchString(d.ExistingPayloadSHA256)
	added := d.Reason == "activated" || d.Reason == "reactivated"
	if d.Outcome != "activated" || !capacityDigest.MatchString(d.PreparationDigest) || !capacityDigest.MatchString(d.PayloadSHA256) || !validState || d.Activated != added || !added && d.Reason != "already_activated" {
		return false, false, errors.New("producer refused schedule authority")
	}
	return true, d.Activated, nil
}

// Attest absence through the directory descriptor, preserving the retained
// client's root-created dormant or producer-owned active directory contract.
func capacityProducerDormant(directory string, uid uint32) error {
	fd, err := syscall.Open(directory, syscall.O_RDONLY|syscall.O_DIRECTORY|syscall.O_CLOEXEC|syscall.O_NOFOLLOW, 0)
	if os.IsNotExist(err) {
		return nil
	}
	if err != nil {
		return errors.New("producer authority directory unavailable")
	}
	f := os.NewFile(uintptr(fd), directory)
	defer f.Close()
	var before, after syscall.Stat_t
	if syscall.Fstat(fd, &before) != nil || before.Mode&syscall.S_IFMT != syscall.S_IFDIR || !(before.Uid == 0 && before.Mode&0777 == 0755 || before.Uid == uid && before.Mode&0777 == 0700) {
		return errors.New("producer authority directory metadata differs")
	}
	names, err := f.Readdirnames(-1)
	if err != nil {
		return errors.New("producer activation marker unavailable")
	}
	for _, name := range names {
		if name == ".activation-v1" {
			return errors.New("durable Go producer blocks legacy enqueue")
		}
	}
	if syscall.Fstat(fd, &after) != nil || before.Dev != after.Dev || before.Ino != after.Ino || before.Uid != after.Uid || before.Mode != after.Mode {
		return errors.New("producer authority directory changed")
	}
	return nil
}

func capacityProducerIdentity(socket string, uid uint32) (uint64, uint64, error) {
	directory, err := os.Lstat(filepath.Dir(socket))
	if err != nil {
		return 0, 0, errors.New("producer directory unavailable")
	}
	info, err := os.Lstat(socket)
	if err != nil {
		return 0, 0, errors.New("producer socket unavailable")
	}
	d, dok := directory.Sys().(*syscall.Stat_t)
	s, sok := info.Sys().(*syscall.Stat_t)
	if !dok || !sok || !directory.IsDir() || directory.Mode()&os.ModeSymlink != 0 || directory.Mode().Perm() != 0700 || d.Uid != uid || info.Mode()&os.ModeSocket == 0 || info.Mode().Perm() != 0600 || s.Uid != uid {
		return 0, 0, errors.New("producer socket metadata differs")
	}
	return uint64(s.Dev), s.Ino, nil
}

func capacityProducerMillis(score float64) (int64, error) {
	if score < 0 || score > 9_999_999_999.999 {
		return 0, errors.New("invalid producer schedule")
	}
	r, ok := new(big.Rat).SetString(strconv.FormatFloat(score, 'f', -1, 64))
	if !ok {
		return 0, errors.New("invalid producer schedule")
	}
	r.Mul(r, big.NewRat(1000, 1))
	q, rem := new(big.Int), new(big.Int)
	q.QuoRem(r.Num(), r.Denom(), rem)
	if rem.Sign() != 0 {
		q.Add(q, big.NewInt(1))
	}
	return q.Int64(), nil
}

func (p capacityProducer) enqueue(ctx context.Context, domain string, score float64, row capacitySchedule, config map[string]string) (bool, bool, error) {
	if p.mode == "off" {
		return false, false, capacityProducerDormant(filepath.Dir(p.socket), p.uid)
	}
	if p.mode != "enabled" {
		return false, false, errors.New("invalid producer mode")
	}
	ms, err := capacityProducerMillis(score)
	if err != nil {
		return false, false, err
	}
	request := map[string]any{"browser": row.Browser, "cohort": "", "config": config, "domain": domain, "expected_digest": "", "first_time": row.Hash == nil, "next_scrape_at_ms": ms, "operation": "enqueue", "operator_transfer": false, "posting_id": row.ID, "version": capacityProducerProtocol}
	response, err := p.exchange(ctx, request)
	if err != nil {
		return false, false, err
	}
	return capacityProducerDecode(response)
}

// One authenticated bounded exchange serves ordinary enqueue and cold
// administration. No failed exchange grants a legacy fallback.
func (p capacityProducer) exchange(ctx context.Context, request map[string]any) ([]byte, error) {
	if p.mode != "enabled" {
		return nil, errors.New("invalid producer mode")
	}
	dev, ino, err := capacityProducerIdentity(p.socket, p.uid)
	if err != nil {
		return nil, err
	}
	payload, err := b0task.CanonicalJSON(request, true)
	if err != nil {
		return nil, errors.New("encode producer request failed")
	}
	record, err := framing.EncodeRecord(payload, 256*1024)
	if err != nil {
		return nil, errors.New("producer request exceeds frame bound")
	}
	ctx, cancel := context.WithTimeout(ctx, 3*time.Second)
	defer cancel()
	connection, err := (&net.Dialer{}).DialContext(ctx, "unix", p.socket)
	if err != nil {
		return nil, errors.New("producer connection unavailable")
	}
	defer connection.Close()
	deadline, _ := ctx.Deadline()
	if connection.SetDeadline(deadline) != nil {
		return nil, errors.New("producer deadline unavailable")
	}
	peer, err := capacityProducerPeerUID(connection.(*net.UnixConn))
	afterDev, afterIno, identityErr := capacityProducerIdentity(p.socket, p.uid)
	if err != nil || peer != p.uid || identityErr != nil || dev != afterDev || ino != afterIno {
		return nil, errors.New("producer peer identity differs")
	}
	if _, err := io.Copy(connection, bytes.NewReader(record)); err != nil {
		return nil, errors.New("producer request exchange failed")
	}
	response, err := framing.ReadRecord(connection, 256*1024)
	if err != nil {
		return nil, errors.New("producer response frame failed")
	}
	var trailing [1]byte
	if n, err := connection.Read(trailing[:]); n != 0 || err != io.EOF {
		return nil, errors.New("producer response has trailing bytes or no close")
	}
	return response, nil
}
