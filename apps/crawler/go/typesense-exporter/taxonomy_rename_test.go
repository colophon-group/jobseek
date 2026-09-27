package main

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"reflect"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
)

func renameText(value string) *string { return &value }
func TestRenameSelectionAndTechnologyOrder(t *testing.T) {
	before := map[int]*string{1: renameText("Old"), 2: renameText("Same"), 3: nil, 4: renameText(""), 5: renameText("Removed"), 7: renameText("Nullified")}
	after := map[int]*string{1: renameText("New"), 2: renameText("Same"), 3: renameText("New"), 4: renameText("New"), 6: renameText("Added"), 7: nil}
	if !reflect.DeepEqual(changedRenameIDs(before, after), []int{1, 7}) {
		t.Fatal("rename detection changed added/deleted/empty semantics")
	}
	docs := renameDocuments("technology", nil, []renamePosting{{"a", []int{2, 1, 2, 3, 4, 99}}, {"b", []int{3, 4, 99}}}, map[int]*string{1: renameText("Go"), 2: renameText("Rust"), 3: nil, 4: renameText("")})
	if len(docs) != 1 || !reflect.DeepEqual(docs[0]["technology_names"], []string{"Rust", "Go", "Rust"}) {
		t.Fatalf("technology ordering or missing-name semantics changed: %#v", docs)
	}
	docs = renameDocuments("occupation", nil, []renamePosting{{ID: "a"}}, nil)
	if value, exists := docs[0]["occupation_name"]; !exists || value != nil {
		t.Fatal("null rename changed")
	}
}
func TestRenameInputRejectsMalformedData(t *testing.T) {
	for _, value := range []string{`null`, `{}`, `{"before":{"unknown":{}}}`, `{"before":{"occupation":{"bad":"name"}}}`, `{"before":{"occupation":{"1":7}}}`, `{"before":{}} {}`, `{"before":{},"extra":1}`, string([]byte{0xff}), strings.Repeat(" ", 8<<20) + "{}"} {
		if _, err := decodeRenameInput(strings.NewReader(value)); err == nil {
			t.Fatal("invalid rename input accepted")
		}
	}
	names, err := decodeRenameInput(strings.NewReader(`{"before":{"technology":{"1":null,"2":"Zürich"}}}`))
	if err != nil || names["technology"][1] != nil || *names["technology"][2] != "Zürich" {
		t.Fatal("valid rename input rejected")
	}
}
func TestRenamePostgresBatchesFenceAndCursorPreservation(t *testing.T) {
	dsn := os.Getenv("GO_TYPESENSE_TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("GO_TYPESENSE_TEST_DATABASE_URL is not set")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 45*time.Second)
	defer cancel()
	conn, err := pgx.Connect(ctx, dsn)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close(context.Background())
	observer, err := pgx.Connect(ctx, dsn)
	if err != nil {
		t.Fatal(err)
	}
	defer observer.Close(context.Background())
	schema := pgx.Identifier{fmt.Sprintf("rename_test_%d", time.Now().UnixNano())}.Sanitize()
	if _, err := conn.Exec(ctx, "CREATE SCHEMA "+schema); err != nil {
		t.Fatal(err)
	}
	defer conn.Exec(context.Background(), "DROP SCHEMA "+schema+" CASCADE")
	for _, db := range []*pgx.Conn{conn, observer} {
		if _, err := db.Exec(ctx, "SET search_path TO "+schema); err != nil {
			t.Fatal(err)
		}
	}
	_, err = conn.Exec(ctx, `
CREATE TABLE occupation(id int);
CREATE TABLE occupation_name(occupation_id int,name text,locale text,is_display bool);
CREATE TABLE seniority(id int);
CREATE TABLE seniority_name(seniority_id int,name text,locale text,is_display bool);
CREATE TABLE technology(id int,name text);
CREATE TABLE job_posting(id uuid PRIMARY KEY,occupation_id int,seniority_id int,technology_ids int[]);
CREATE TABLE exporter_state(key text PRIMARY KEY,value text);
INSERT INTO exporter_state VALUES('last_export_ts:typesense:job_posting','cursor-do-not-touch'),('export_owner:typesense:job_posting','go');
INSERT INTO occupation VALUES(1);
INSERT INTO occupation_name VALUES(1,'Old occupation','en',true);
INSERT INTO seniority VALUES(1);
INSERT INTO seniority_name VALUES(1,'Old seniority','en',true);
INSERT INTO technology VALUES(1,'Old language'),(2,'Rust');
INSERT INTO job_posting SELECT ('00000000-0000-0000-0000-'||lpad(n::text,12,'0'))::uuid,1,1,ARRAY[1,2,NULL,1,999] FROM generate_series(1,1001)n;
INSERT INTO job_posting VALUES('ffffffff-ffff-ffff-ffff-ffffffffffff',2,NULL,ARRAY[2]);
`)
	if err != nil {
		t.Fatal(err)
	}
	before, err := loadRenameNames(ctx, conn)
	if err != nil {
		t.Fatal(err)
	}
	_, err = conn.Exec(ctx, `UPDATE occupation_name SET name='Engineer';UPDATE seniority_name SET name='Senior';UPDATE technology SET name='Go' WHERE id=1`)
	if err != nil {
		t.Fatal(err)
	}
	var mutex sync.Mutex
	calls := 0
	malformed := false
	documents := map[string]map[string]any{}
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		mutex.Lock()
		defer mutex.Unlock()
		calls++
		var acquired bool
		if err := observer.QueryRow(ctx, "SELECT pg_try_advisory_lock($1)", exportCursorFenceID).Scan(&acquired); err != nil || acquired {
			t.Errorf("rename did not hold cursor fence: %v %v", acquired, err)
		}
		if r.URL.Path != "/collections/job_posting/documents/import" || r.URL.Query().Get("action") != "update" {
			t.Error("rename wrote wrong target")
		}
		decoder := json.NewDecoder(r.Body)
		count := 0
		for decoder.More() {
			var doc map[string]any
			if err := decoder.Decode(&doc); err != nil {
				t.Error(err)
				return
			}
			count++
			id := doc["id"].(string)
			if documents[id] == nil {
				documents[id] = map[string]any{}
			}
			for key, value := range doc {
				documents[id][key] = value
			}
		}
		// Consume the complete HTTP/1 request before writing acknowledgements.
		for index := 0; index < count; index++ {
			if malformed {
				fmt.Fprintln(w, `{}`)
			} else {
				fmt.Fprintln(w, `{"success":true}`)
			}
		}
		if count < 1 || count > 1000 {
			t.Errorf("rename batch unbounded: %d", count)
		}
	}))
	defer server.Close()
	reader := taxonomyReader{Client: server.Client(), BaseURL: server.URL, Key: "fixture"}
	if err := applyRenameUpdates(ctx, conn, reader, before); err != nil {
		t.Fatal(err)
	}
	mutex.Lock()
	if calls != 6 || len(documents) != 1001 {
		t.Fatalf("rename coverage wrong: calls=%d docs=%d", calls, len(documents))
	}
	for _, doc := range documents {
		if doc["occupation_name"] != "Engineer" || doc["seniority_name"] != "Senior" || !reflect.DeepEqual(doc["technology_names"], []any{"Go", "Rust", "Go"}) {
			t.Fatal("renamed payload differs")
		}
	}
	malformed = true
	calls = 0
	mutex.Unlock()
	if err := applyRenameUpdates(ctx, conn, reader, before); err == nil {
		t.Fatal("ambiguous acknowledgement accepted")
	}
	mutex.Lock()
	if calls != 1 {
		t.Fatal("continued after ambiguous acknowledgement")
	}
	mutex.Unlock()
	var position, owner string
	if err := conn.QueryRow(ctx, "SELECT value FROM exporter_state WHERE key=$1", typesenseCursorKey).Scan(&position); err != nil {
		t.Fatal(err)
	}
	if err := conn.QueryRow(ctx, "SELECT value FROM exporter_state WHERE key=$1", typesenseOwnerKey).Scan(&owner); err != nil {
		t.Fatal(err)
	}
	if position != "cursor-do-not-touch" || owner != "go" {
		t.Fatal("rename moved cursor or owner")
	}
	var released bool
	if err := observer.QueryRow(ctx, "SELECT pg_try_advisory_lock($1)", exportCursorFenceID).Scan(&released); err != nil || !released {
		t.Fatal("rename failure leaked fence")
	}
	observer.Exec(ctx, "SELECT pg_advisory_unlock($1)", exportCursorFenceID)
}
