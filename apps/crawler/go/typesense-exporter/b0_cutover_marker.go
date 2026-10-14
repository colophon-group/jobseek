package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"io"
	"os"
	"regexp"
	"strconv"

	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/b0task"
	"golang.org/x/sys/unix"
)

var errB0Cutover = errors.New("B0 cold cutover administration rejected")
var b0ProducerID = regexp.MustCompile(`^[a-z0-9][a-z0-9._-]{0,127}$`)

type b0CutoverIdentity struct {
	Namespace, Shard, Cohort, SourceReceipt, PlanDigest string
	Epoch                                               int64
}

// The command uses the fixed producer directory and UID. Parameters allow
// isolated fixtures to exercise the same descriptor and canonical-byte checks.
func b0ProducerMarkerPhase(directory string, uid uint32, identity b0CutoverIdentity) (string, error) {
	fd, err := unix.Open(directory, unix.O_RDONLY|unix.O_DIRECTORY|unix.O_CLOEXEC|unix.O_NOFOLLOW, 0)
	if os.IsNotExist(err) {
		return "absent", nil
	}
	if err != nil {
		return "", errB0Cutover
	}
	defer unix.Close(fd)
	var dir unix.Stat_t
	if unix.Fstat(fd, &dir) != nil {
		return "", errB0Cutover
	}
	if dir.Mode&unix.S_IFMT != unix.S_IFDIR || !(dir.Uid == 0 && dir.Mode&0777 == 0755 || dir.Uid == uid && dir.Mode&0777 == 0700) {
		return "unsafe", nil
	}
	markerFD, err := unix.Openat(fd, ".activation-v1", unix.O_RDONLY|unix.O_CLOEXEC|unix.O_NOFOLLOW|unix.O_NONBLOCK, 0)
	if os.IsNotExist(err) {
		return "absent", nil
	}
	if err != nil {
		return "unsafe", nil
	}
	f := os.NewFile(uintptr(markerFD), ".activation-v1")
	defer f.Close()
	var before, after unix.Stat_t
	if unix.Fstat(markerFD, &before) != nil {
		return "unsafe", nil
	}
	if before.Mode&unix.S_IFMT != unix.S_IFREG || before.Mode&0777 != 0600 || before.Uid != uid || before.Nlink != 1 || before.Size < 0 || before.Size > 4096 {
		return "unsafe", nil
	}
	payload, err := io.ReadAll(io.LimitReader(f, 4097))
	if err != nil || unix.Fstat(markerFD, &after) != nil || int64(len(payload)) != before.Size || before.Dev != after.Dev || before.Ino != after.Ino || before.Size != after.Size {
		return "unsafe", nil
	}
	return b0ProducerMarkerPayloadPhase(payload, identity), nil
}

func b0ProducerMarkerPayloadPhase(payload []byte, identity b0CutoverIdentity) string {
	if len(payload) == 0 || bytes.Equal(payload, []byte("P")) || bytes.HasPrefix(payload, []byte("P\n")) && !bytes.HasSuffix(payload, []byte("\n")) {
		return "partial"
	}
	if len(payload) < 3 || !bytes.HasSuffix(payload, []byte("\n")) || !(bytes.HasPrefix(payload, []byte("P\n")) || bytes.HasPrefix(payload, []byte("A\n"))) {
		return "unsafe"
	}
	decoder := json.NewDecoder(bytes.NewReader(payload[2 : len(payload)-1]))
	decoder.UseNumber()
	var document map[string]any
	if decoder.Decode(&document) != nil || len(document) != 7 {
		return "unsafe"
	}
	for _, key := range []string{"board_slugs", "cohort", "engine_owner", "namespace", "routing_epoch", "schema", "shard_id"} {
		if _, present := document[key]; !present {
			return "unsafe"
		}
	}
	if document["schema"] != "jobseek.lightpanda.producer-activation/v1" || document["cohort"] != identity.Cohort || document["engine_owner"] != "go" || document["namespace"] != identity.Namespace || document["shard_id"] != identity.Shard || document["routing_epoch"] != json.Number(strconv.FormatInt(identity.Epoch, 10)) {
		return "unsafe"
	}
	values, ok := document["board_slugs"].([]any)
	if !ok || len(values) < 1 || len(values) > 16 {
		return "unsafe"
	}
	previous := ""
	for _, value := range values {
		slug, ok := value.(string)
		if !ok || !b0ProducerID.MatchString(slug) || slug <= previous {
			return "unsafe"
		}
		previous = slug
	}
	canonical, err := b0task.CanonicalJSON(document, true)
	if err != nil || !bytes.Equal(canonical, payload[2:len(payload)-1]) {
		return "unsafe"
	}
	if payload[0] == 'P' {
		return "preparing"
	}
	return "active"
}

func b0RollbackMarkerValid(receipt, phase string, namespacePresent bool) bool {
	if !namespacePresent {
		return receipt == "pending" && (phase == "absent" || phase == "preparing" || phase == "partial")
	}
	return receipt == "active" && phase == "active" || receipt == "pending" && (phase == "active" || phase == "preparing")
}
