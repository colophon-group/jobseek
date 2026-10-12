package claimantbundle

import (
	"os"
	"syscall"
)

func changedTime(info os.FileInfo) int64 {
	st := info.Sys().(*syscall.Stat_t)
	return st.Ctimespec.Sec*1e9 + st.Ctimespec.Nsec
}
