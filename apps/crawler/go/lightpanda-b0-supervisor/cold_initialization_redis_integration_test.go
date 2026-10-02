//go:build integration

package main

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"reflect"
	"testing"
)

func TestColdProducerNativeInitializationRealRedisSaveAndReplay(t *testing.T) {
	client, queue, owner := integrationRedisQueue(t)
	ctx := context.Background()
	if err := client.FlushDB(ctx).Err(); err != nil {
		t.Fatal(err)
	}
	// Unrelated legacy scheduling remains byte-for-byte unchanged.
	if err := client.Set(ctx, "cold-fixture-legacy", "1925089445.100001", 0).Err(); err != nil {
		t.Fatal(err)
	}
	sentinel, err := newProducerActivationSentinel(filepath.Join(t.TempDir(), ".activation-v1"), owner, uint32(os.Geteuid()))
	if err != nil {
		t.Fatal(err)
	}
	p := &b0Producer{queue: queue, owner: owner, sentinel: sentinel}
	prefix := filepath.Join(filepath.Dir(sentinel.path), "history")
	body := []byte("approved synthetic cold host request")
	saves := 0
	save := func(ctx context.Context) error {
		saves++
		reply, err := client.Save(ctx).Result()
		if err != nil || reply != "OK" {
			return errors.New("SAVE refused")
		}
		return nil
	}
	if err := client.Do(ctx, "ACL", "SETUSER", "default", "-save").Err(); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = client.Do(ctx, "ACL", "SETUSER", "default", "+save").Err() })
	if initializeColdProducer(ctx, p, body, prefix, save) == nil {
		t.Fatal("denied SAVE was admitted")
	}
	if _, err := os.Lstat(prefix + ".complete"); !os.IsNotExist(err) {
		t.Fatal("denied SAVE wrote completion")
	}
	if err := client.Do(ctx, "ACL", "SETUSER", "default", "+save").Err(); err != nil {
		t.Fatal(err)
	}
	if err := initializeColdProducer(ctx, p, body, prefix, save); err != nil {
		t.Fatal(err)
	}
	keys, err := client.Keys(ctx, "*").Result()
	if err != nil {
		t.Fatal(err)
	}
	if len(keys) != 3 {
		t.Fatal("initializer created task/queue effects", keys)
	}
	before, err := client.HGetAll(ctx, producerOwnerKey).Result()
	if err != nil {
		t.Fatal(err)
	}
	if err := initializeColdProducer(ctx, p, body, prefix, save); err != nil {
		t.Fatal(err)
	}
	after, err := client.HGetAll(ctx, producerOwnerKey).Result()
	if err != nil || !reflect.DeepEqual(before, after) || saves != 2 {
		t.Fatal("exact replay repeated SAVE or altered owner")
	}
	legacy, err := client.Get(ctx, "cold-fixture-legacy").Result()
	if err != nil || legacy != "1925089445.100001" {
		t.Fatal("cold bootstrap changed legacy schedule")
	}
	if err := os.Remove(sentinel.path); err != nil {
		t.Fatal(err)
	}
	if initializeColdProducer(ctx, p, body, prefix, save) == nil {
		t.Fatal("orphan owner silently adopted after sentinel loss")
	}
}
