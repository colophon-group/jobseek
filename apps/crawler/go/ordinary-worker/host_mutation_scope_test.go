package worker

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"syscall"
	"testing"
)

func assertHostScopeFlock(t *testing.T, path string, held bool) {
	t.Helper()
	f, err := os.OpenFile(path, os.O_RDONLY, 0)
	if err != nil {
		t.Fatal("private lock observation", err)
	}
	defer f.Close()
	err = syscall.Flock(int(f.Fd()), syscall.LOCK_EX|syscall.LOCK_NB)
	if err == nil {
		defer syscall.Flock(int(f.Fd()), syscall.LOCK_UN)
	}
	if held && !errors.Is(err, syscall.EWOULDBLOCK) || !held && err != nil {
		t.Fatal("original host flock lifetime mismatch", held, err)
	}
}

func TestHostMutationScopeKeepsOriginalDescriptorAcrossSequentialPhases(t *testing.T) {
	path := filepath.Join(hostPrivateDirectory(t), "mutation.lock")
	var escaped context.Context
	err := withHostMutationScope(context.Background(), path, func(ctx context.Context) error {
		escaped = ctx
		s := ctx.Value(hostMutationScopeKey{}).(*hostMutationScope)
		original := s.lock.file.Fd()
		for i := 0; i < 3; i++ {
			lock, release, err := acquireHostPhaseLock(ctx, path)
			if err != nil || lock.file.Fd() != original || lock != s.lock || CheckHostMutationScope(ctx) != nil {
				t.Fatal("phase reopened or lost original descriptor", err)
			}
			if other, done, err := acquireHostPhaseLock(ctx, path); err == nil || other != nil || done != nil {
				t.Fatal("parallel phase adopted held lifecycle")
			}
			assertHostScopeFlock(t, path, true)
			release()
			release() // Closing a borrowed phase twice cannot release the owner.
			assertHostScopeFlock(t, path, true)
		}
		return nil
	})
	if err != nil || CheckHostMutationScope(escaped) == nil {
		t.Fatal("scoped lifecycle completion/escape", err)
	}
	if lock, done, err := acquireHostPhaseLock(escaped, path); err == nil || lock != nil || done != nil {
		t.Fatal("escaped scope reacquired authority")
	}
	assertHostScopeFlock(t, path, false)
}

func TestHostMutationScopeRefusesNestedForeignCancelledAndSubstitutedAuthority(t *testing.T) {
	for _, fault := range []string{"nested", "different path", "symlink", "cancelled", "replacement", "unfinished phase"} {
		t.Run(fault, func(t *testing.T) {
			path := filepath.Join(hostPrivateDirectory(t), "mutation.lock")
			err := withHostMutationScope(context.Background(), path, func(ctx context.Context) error {
				switch fault {
				case "nested":
					if withHostMutationScope(ctx, path, func(context.Context) error { t.Fatal("nested lifecycle ran"); return nil }) == nil {
						t.Fatal("nested lifecycle admitted")
					}
				case "different path", "symlink", "cancelled":
					other := filepath.Join(filepath.Dir(path), "other.lock")
					if fault == "symlink" && os.Symlink(path, other) != nil {
						t.Fatal("private alias fixture")
					}
					if fault == "cancelled" {
						var cancel context.CancelFunc
						ctx, cancel = context.WithCancel(ctx)
						cancel()
						other = path
					}
					if lock, done, err := acquireHostPhaseLock(ctx, other); err == nil || lock != nil || done != nil {
						t.Fatal("foreign/expired phase adopted scope")
					}
				case "replacement":
					if os.Rename(path, path+".original") != nil || os.WriteFile(path, nil, 0600) != nil {
						t.Fatal("private replacement fixture")
					}
					if CheckHostMutationScope(ctx) == nil {
						t.Fatal("substituted inode granted live scope")
					}
				case "unfinished phase":
					if _, _, err := acquireHostPhaseLock(ctx, path); err != nil {
						t.Fatal(err)
					}
				}
				return nil
			})
			if (fault == "replacement" || fault == "unfinished phase") != (err != nil) {
				t.Fatal("lifecycle lost final validation", fault, err)
			}
			assertHostScopeFlock(t, path, false)
		})
	}
	if acquire, done, err := acquireHostPhaseLock(context.WithValue(context.Background(), hostMutationScopeKey{}, "foreign"), "/unused"); err == nil || acquire != nil || done != nil {
		t.Fatal("untyped context granted fallback acquisition")
	}
}

func TestHostMutationScopeFailureReleasesLockWithoutSuppressingFailure(t *testing.T) {
	path := filepath.Join(hostPrivateDirectory(t), "mutation.lock")
	want := errors.New("private lifecycle failure")
	if err := withHostMutationScope(context.Background(), path, func(context.Context) error { return want }); !errors.Is(err, want) {
		t.Fatal("lifecycle hid phase failure", err)
	}
	assertHostScopeFlock(t, path, false)
	if CheckHostMutationScope(context.Background()) == nil || withHostMutationScope(nil, path, func(context.Context) error { return nil }) == nil || withHostMutationScope(context.Background(), path, nil) == nil {
		t.Fatal("missing explicit scope admitted")
	}
}
