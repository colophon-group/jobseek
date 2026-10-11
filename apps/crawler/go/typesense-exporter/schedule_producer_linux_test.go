//go:build linux

package main

import (
	"context"
	"encoding/json"
	"io"
	"net"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/b0task"
	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/framing"
)

func TestCapacityProducerAuthenticatedLinuxExchange(t *testing.T) {
	for _, outcome := range []string{"legacy", "activated", "already_activated", "capacity", "trailing", "malformed", "authority_lost"} {
		t.Run(outcome, func(t *testing.T) {
			directory := t.TempDir()
			if err := os.Chmod(directory, 0700); err != nil {
				t.Fatal(err)
			}
			socket := filepath.Join(directory, "control.sock")
			listener, err := net.ListenUnix("unix", &net.UnixAddr{Name: socket, Net: "unix"})
			if err != nil {
				t.Fatal(err)
			}
			defer listener.Close()
			if err := os.Chmod(socket, 0600); err != nil {
				t.Fatal(err)
			}
			server := make(chan error, 1)
			go func() {
				connection, err := listener.AcceptUnix()
				if err != nil {
					server <- err
					return
				}
				defer connection.Close()
				_ = connection.SetDeadline(time.Now().Add(3 * time.Second))
				payload, err := framing.ReadRecord(connection, 256*1024)
				if err != nil {
					server <- err
					return
				}
				var request map[string]any
				if err = json.Unmarshal(payload, &request); err != nil {
					server <- err
					return
				}
				canonical, err := b0task.CanonicalJSON(request, true)
				if err != nil || string(canonical) != string(payload) {
					server <- io.ErrUnexpectedEOF
					return
				}
				want := map[string]any{"browser": true, "cohort": "", "config": map[string]any{"source_url": "https://jobs.example/ümlaut", "board_id": "board", "description_r2_hash": "hash", "scrape_step": "0"}, "domain": "jobs.example", "expected_digest": "", "first_time": false, "next_scrape_at_ms": float64(1234001), "operation": "enqueue", "operator_transfer": false, "posting_id": "posting", "version": capacityProducerProtocol}
				if !reflect.DeepEqual(request, want) {
					server <- io.ErrUnexpectedEOF
					return
				}
				d := capacityProducerDecision{Version: capacityProducerProtocol, Outcome: "legacy", Reason: "literal_legacy", BoardSlugs: []string{}, LifetimeCapacity: 2048, LifetimeHeadroom: 2048}
				if outcome == "activated" || outcome == "already_activated" {
					d.Outcome = "activated"
					d.Reason = outcome
					d.Activated = outcome == "activated"
					d.PreparationDigest = strings.Repeat("a", 64)
					d.PayloadSHA256 = strings.Repeat("b", 64)
				}
				if outcome == "capacity" {
					d.Outcome = "capacity"
					d.Reason = "namespace_full"
					d.LifetimeOccupancy = 2048
					d.LifetimeHeadroom = 0
				}
				if outcome == "authority_lost" {
					d.Outcome = "error"
					d.Reason = "authority_lost"
				}
				response, err := b0task.CanonicalJSON(d, true)
				if err != nil {
					server <- err
					return
				}
				record, err := framing.EncodeRecord(response, 256*1024)
				if err != nil {
					server <- err
					return
				}
				if outcome == "trailing" {
					record = append(record, 0)
				}
				if outcome == "malformed" {
					record = []byte{0x80, 0x00}
				}
				_, err = connection.Write(record)
				server <- err
			}()
			p := capacityProducer{mode: "enabled", socket: socket, uid: uint32(os.Getuid())}
			hash := "hash"
			row := capacitySchedule{ID: "posting", URL: "https://jobs.example/ümlaut", Board: "board", Hash: &hash, Browser: true}
			config := map[string]string{"source_url": row.URL, "board_id": row.Board, "description_r2_hash": hash, "scrape_step": "0"}
			owned, added, err := p.enqueue(context.Background(), "jobs.example", 1234.0001, row, config)
			if serverErr := <-server; serverErr != nil {
				t.Fatal("authenticated request differs", serverErr)
			}
			switch outcome {
			case "legacy":
				if err != nil || owned || added {
					t.Fatal(owned, added, err)
				}
			case "activated":
				if err != nil || !owned || !added {
					t.Fatal(owned, added, err)
				}
			case "already_activated":
				if err != nil || !owned || added {
					t.Fatal(owned, added, err)
				}
			default:
				if err == nil {
					t.Fatal("producer failure granted legacy authority", outcome)
				}
			}
		})
	}
}

func TestCapacityProducerLinuxMetadataRefusal(t *testing.T) {
	directory := t.TempDir()
	socket := filepath.Join(directory, "control.sock")
	listener, err := net.ListenUnix("unix", &net.UnixAddr{Name: socket, Net: "unix"})
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	if err := os.Chmod(directory, 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(socket, 0600); err != nil {
		t.Fatal(err)
	}
	uid := uint32(os.Getuid())
	if _, _, err := capacityProducerIdentity(socket, uid); err != nil {
		t.Fatal(err)
	}
	if _, _, err := capacityProducerIdentity(socket, uid+1); err == nil {
		t.Fatal("foreign owner accepted")
	}
	if err := os.Chmod(socket, 0666); err != nil {
		t.Fatal(err)
	}
	if _, _, err := capacityProducerIdentity(socket, uid); err == nil {
		t.Fatal("unsafe socket mode accepted")
	}
	if err := os.Chmod(socket, 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(directory, 0755); err != nil {
		t.Fatal(err)
	}
	if _, _, err := capacityProducerIdentity(socket, uid); err == nil {
		t.Fatal("unsafe authority directory accepted")
	}
}
