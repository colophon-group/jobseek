package main

import (
	"context"
	"encoding/json"
	"flag"
	"io"
	"os"
	"os/signal"
	"strconv"
	"syscall"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/redis/go-redis/v9"
)

type b0PlanOptions struct {
	Command, Operation, Cohort, ReceiptState, Receipt, Digest string
	Apply                                                     bool
}

func parseB0PlanOptions(args []string) (b0PlanOptions, error) {
	o := b0PlanOptions{}
	if len(args) == 0 {
		return o, errB0Cutover
	}
	o.Command = args[0]
	if o.Command != "plan" && o.Command != "activate" && o.Command != "rollback" {
		return o, errB0Cutover
	}
	f := flag.NewFlagSet(o.Command, flag.ContinueOnError)
	f.SetOutput(io.Discard)
	f.StringVar(&o.Cohort, "cohort", "", "")
	if o.Command == "plan" {
		f.StringVar(&o.Operation, "operation", "", "")
		f.StringVar(&o.ReceiptState, "receipt-state", "", "")
		f.StringVar(&o.Receipt, "source-receipt-sha256", "", "")
	} else {
		o.Operation = o.Command
		f.BoolVar(&o.Apply, "apply", false, "")
		f.StringVar(&o.Digest, "expect-digest", "", "")
		if o.Command == "rollback" {
			f.StringVar(&o.ReceiptState, "receipt-state", "", "")
			f.StringVar(&o.Receipt, "source-receipt-sha256", "", "")
		}
	}
	if f.Parse(args[1:]) != nil || f.NArg() != 0 || !(o.Cohort == "c1" || o.Cohort == "c2" || o.Cohort == "c3" || o.Cohort == "cdom") || o.Operation != "activate" && o.Operation != "rollback" {
		return o, errB0Cutover
	}
	if o.Command != "plan" && (!o.Apply || !capacityDigest.MatchString(o.Digest)) {
		return o, errB0Cutover
	}
	if o.Operation == "rollback" && (!(o.ReceiptState == "active" || o.ReceiptState == "pending") || !capacityDigest.MatchString(o.Receipt)) {
		return o, errB0Cutover
	}
	return o, nil
}

func runB0PlanApply(args []string) error {
	o, err := parseB0PlanOptions(args)
	if err != nil {
		return err
	}
	mode := os.Getenv("LIGHTPANDA_B0_PRODUCER_MODE")
	if mode == "" {
		mode = "off"
	}
	if o.Operation == "activate" && mode != "enabled" || o.Operation == "rollback" && mode != "off" {
		return errB0Cutover
	}
	if socket := os.Getenv("LIGHTPANDA_B0_PRODUCER_SOCKET"); o.Operation == "activate" && socket != "" && socket != capacityProducerSocket {
		return errB0Cutover
	}
	epoch, err := strconv.ParseInt(os.Getenv("LIGHTPANDA_B0_ROUTING_EPOCH"), 10, 64)
	if err != nil || strconv.FormatInt(epoch, 10) != os.Getenv("LIGHTPANDA_B0_ROUTING_EPOCH") {
		return errB0Cutover
	}
	identity := b0CutoverIdentity{Namespace: os.Getenv("LIGHTPANDA_B0_QUEUE_NAMESPACE"), Shard: os.Getenv("LIGHTPANDA_B0_SHARD_ID"), Cohort: o.Cohort, Epoch: epoch, SourceReceipt: o.Receipt}
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()
	ctx, cancel := context.WithTimeout(ctx, 90*time.Second)
	defer cancel()
	rawURL := os.Getenv("REDIS_URL")
	if rawURL == "" {
		rawURL = "redis://localhost:6379/0"
	}
	options, err := redis.ParseURL(rawURL)
	if err != nil {
		return errB0Cutover
	}
	options.MaxRetries = -1
	options.DialTimeout = 5 * time.Second
	options.ReadTimeout = 10 * time.Second
	options.ContextTimeoutEnabled = true
	client := redis.NewClient(options)
	defer client.Close()
	q, err := newB0PlanQueue(client, identity)
	if err != nil {
		return err
	}
	role := os.Getenv("CRAWLER_DB_ROLE")
	if role == "" {
		role = "lightpanda-b0-activation"
	}
	dsn := os.Getenv("LOCAL_DATABASE_URL")
	if dsn == "" {
		return errB0Cutover
	}
	config, err := providerCutoverPoolConfig(dsn, role)
	if err != nil {
		return errB0Cutover
	}
	pool, err := pgxpool.NewWithConfig(ctx, config)
	if err != nil {
		return errB0Cutover
	}
	defer pool.Close()
	a := b0CutoverAdmin{pool: pool, queue: q, producer: capacityProducer{mode: mode, socket: capacityProducerSocket, uid: 10001}, mode: mode, markerDirectory: "/run/jobseek-lightpanda-producer", markerUID: 10001}
	var result map[string]any
	if o.Command == "plan" {
		var plan b0CutoverPlan
		if o.Operation == "activate" {
			plan, err = a.activationPlan(ctx)
		} else {
			plan, err = a.rollbackPlan(ctx, o.ReceiptState)
		}
		if err != nil {
			return err
		}
		result = map[string]any{"digest": plan.Digest, "count": len(plan.Tasks), "plan": plan.Document}
		if o.Operation == "rollback" {
			result["rollback_plan_digest"] = plan.RollbackDigest
		}
	} else {
		if o.Operation == "activate" {
			result, err = a.applyActivation(ctx, o.Digest)
		} else {
			result, err = a.applyRollback(ctx, o.ReceiptState, o.Digest)
		}
		if err != nil {
			return err
		}
		result["operation"] = o.Operation
		result["cohort"] = o.Cohort
	}
	return json.NewEncoder(os.Stdout).Encode(result)
}
