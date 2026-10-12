package main

import (
	"errors"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"
)

var generationName = regexp.MustCompile(`^((release-|data-)[A-Za-z0-9._-]+|(murmur|legacy)\.[A-Za-z0-9._-]+)$`)
var candidateName = regexp.MustCompile(`^[0-9a-f]{40}-[1-9][0-9]*-[1-9][0-9]*$`)
var forwardName = regexp.MustCompile(`^\.crawler-forward-data-[0-9a-f]{40}\.[A-Za-z0-9]+$`)

var pruneCrawler = regexp.MustCompile(`^ghcr\.io/[A-Za-z0-9._-]+/jobseek-crawler@sha256:[0-9a-f]{64}$`)
var pruneBrowser = regexp.MustCompile(`^ghcr\.io/[A-Za-z0-9._-]+/jobseek-crawler-browser@sha256:[0-9a-f]{64}$`)

var errPrune = errors.New("publication retention input is unavailable or unsafe")

type retainedGeneration struct {
	path     string
	modified time.Time
}

func pruneDirectory(path string) error {
	info, err := os.Lstat(path)
	if err != nil || !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
		return errPrune
	}
	return nil
}

// The original host wrapper authenticates the verified generation list and
// owns the global mutation lock. Retain its active/journal/data/runtime windows.
func prunePublications(args []string, now time.Time) error {
	if len(args) < 11 {
		return errPrune
	}
	releaseRoot, activePointer, journal, candidateRoot, deployRoot := args[0], args[1], args[2], args[3], args[4]
	keep, e1 := strconv.Atoi(strings.TrimSpace(args[5]))
	grace, e2 := strconv.Atoi(strings.TrimSpace(args[6]))
	count, e3 := strconv.Atoi(strings.TrimSpace(args[10]))
	paths := args[11:]
	if e1 != nil || e2 != nil || e3 != nil || keep < 1 || keep > 20 || grace < 0 || grace > 604800 || count < 0 || count > len(paths) {
		return errPrune
	}
	if pruneDirectory(releaseRoot) != nil || pruneDirectory(deployRoot) != nil {
		return errPrune
	}
	validate := func(path string) (string, error) {
		path = filepath.Clean(path)
		if filepath.Dir(path) != filepath.Clean(releaseRoot) || !generationName.MatchString(filepath.Base(path)) || pruneDirectory(path) != nil {
			return "", errPrune
		}
		return path, nil
	}
	activeInfo, err := os.Lstat(activePointer)
	if err != nil || activeInfo.Mode()&os.ModeSymlink == 0 {
		return errPrune
	}
	active, err := os.Readlink(activePointer)
	if err != nil {
		return errPrune
	}
	active, err = validate(active)
	if err != nil {
		return err
	}
	protected := map[string]bool{active: true}
	info, err := os.Lstat(journal)
	if err == nil {
		if !info.Mode().IsRegular() || info.Mode().Perm() != 0600 {
			return errPrune
		}
		body, err := bridgeRead(filepath.Dir(journal), filepath.Base(journal))
		if err != nil {
			return errPrune
		}
		lines, err := bridgeLines(body)
		if err != nil {
			return errPrune
		}
		values := map[string]string{}
		for _, line := range lines {
			key, value, ok := strings.Cut(line, "=")
			if !ok {
				return errPrune
			}
			if _, exists := values[key]; exists {
				return errPrune
			}
			values[key] = value
		}
		if len(values) > 0 {
			if len(values) != 5 || values["PUBLICATION_FORMAT_VERSION"] != "1" || (values["SYNC_SUCCEEDED"] != "0" && values["SYNC_SUCCEEDED"] != "1") || (values["RECOVERY_ACTION"] != "restore-previous" && values["RECOVERY_ACTION"] != "promote-target") {
				return errPrune
			}
			for _, key := range []string{"PREVIOUS_RELEASE", "TARGET_RELEASE"} {
				path, err := validate(values[key])
				if err != nil {
					return err
				}
				protected[path] = true
			}
		}
	} else if !os.IsNotExist(err) {
		return errPrune
	}
	for _, path := range paths[:count] {
		if path != "" {
			path, err := validate(path)
			if err != nil {
				return err
			}
			protected[path] = true
		}
	}
	verified := map[string]bool{}
	for _, path := range paths[count:] {
		path, err := validate(path)
		if err != nil {
			return err
		}
		verified[path] = true
	}
	protectedCandidate, consumedCandidate, forward := args[7], args[8], args[9]
	if protectedCandidate != "" && !candidateName.MatchString(protectedCandidate) || consumedCandidate != "" && !candidateName.MatchString(consumedCandidate) {
		return errPrune
	}
	candidateExists := false
	if _, err := os.Lstat(candidateRoot); err == nil {
		if pruneDirectory(candidateRoot) != nil {
			return errPrune
		}
		candidateExists = true
	} else if !os.IsNotExist(err) || protectedCandidate != "" || consumedCandidate != "" {
		return errPrune
	}
	if forward != "" {
		forward = filepath.Clean(forward)
		if filepath.Dir(forward) != filepath.Clean(deployRoot) || !forwardName.MatchString(filepath.Base(forward)) || pruneDirectory(forward) != nil {
			return errPrune
		}
	}
	entries, err := os.ReadDir(releaseRoot)
	if err != nil {
		return errPrune
	}
	generations := []retainedGeneration{}
	for _, entry := range entries {
		if !generationName.MatchString(entry.Name()) || entry.Type()&os.ModeSymlink != 0 || !entry.IsDir() {
			continue
		}
		info, err := entry.Info()
		if err != nil {
			return errPrune
		}
		generations = append(generations, retainedGeneration{filepath.Join(releaseRoot, entry.Name()), info.ModTime()})
	}
	sort.Slice(generations, func(i, j int) bool {
		if generations[i].modified.Equal(generations[j].modified) {
			return filepath.Base(generations[i].path) > filepath.Base(generations[j].path)
		}
		return generations[i].modified.After(generations[j].modified)
	})
	newest := 0
	pairCount := 0
	pairs := map[string]bool{}
	for _, generation := range generations {
		if !verified[generation.path] {
			continue
		}
		if newest < keep {
			protected[generation.path] = true
			newest++
		}
		if pairCount >= 3 {
			continue
		}
		body, err := bridgeRead(generation.path, "success.env")
		if err != nil || len(body) > 65536 {
			return errPrune
		}
		lines, err := bridgeLines(body)
		if err != nil {
			return errPrune
		}
		values := map[string]string{}
		for _, line := range lines {
			key, value, ok := strings.Cut(line, "=")
			if !ok || (key != "CRAWLER_IMAGE_REF" && key != "BROWSER_IMAGE_REF") {
				continue
			}
			if _, exists := values[key]; exists {
				return errPrune
			}
			values[key] = value
		}
		if len(values) == 0 {
			continue
		}
		if len(values) != 2 || !pruneCrawler.MatchString(values["CRAWLER_IMAGE_REF"]) || !pruneBrowser.MatchString(values["BROWSER_IMAGE_REF"]) {
			return errPrune
		}
		pair := values["CRAWLER_IMAGE_REF"] + "\n" + values["BROWSER_IMAGE_REF"]
		if !pairs[pair] {
			pairs[pair] = true
			pairCount++
			protected[generation.path] = true
		}
	}
	deleted := false
	for _, generation := range generations {
		if protected[generation.path] || now.Sub(generation.modified) < time.Duration(grace)*time.Second {
			continue
		}
		if err := os.RemoveAll(generation.path); err != nil {
			return err
		}
		deleted = true
	}
	if deleted {
		if err := syncDirectory(releaseRoot); err != nil {
			return err
		}
	}
	if candidateExists {
		deleted = false
		if consumedCandidate != "" {
			path := filepath.Join(candidateRoot, consumedCandidate)
			if _, err := os.Lstat(path); err == nil {
				if pruneDirectory(path) != nil {
					return errPrune
				}
				if err := os.RemoveAll(path); err != nil {
					return err
				}
				deleted = true
			} else if !os.IsNotExist(err) {
				return errPrune
			}
		}
		entries, err := os.ReadDir(candidateRoot)
		if err != nil {
			return errPrune
		}
		for _, entry := range entries {
			if !candidateName.MatchString(entry.Name()) || entry.Type()&os.ModeSymlink != 0 || !entry.IsDir() || entry.Name() == protectedCandidate {
				continue
			}
			info, err := entry.Info()
			if err != nil {
				return errPrune
			}
			if now.Sub(info.ModTime()) < time.Duration(grace)*time.Second {
				continue
			}
			if err := os.RemoveAll(filepath.Join(candidateRoot, entry.Name())); err != nil {
				return err
			}
			deleted = true
		}
		if deleted {
			if err := syncDirectory(candidateRoot); err != nil {
				return err
			}
		}
	}
	deleted = false
	entries, err = os.ReadDir(deployRoot)
	if err != nil {
		return errPrune
	}
	for _, entry := range entries {
		if !forwardName.MatchString(entry.Name()) || entry.Type()&os.ModeSymlink != 0 || !entry.IsDir() {
			continue
		}
		path := filepath.Join(deployRoot, entry.Name())
		if path == forward {
			continue
		}
		info, err := entry.Info()
		if err != nil {
			return errPrune
		}
		if now.Sub(info.ModTime()) < time.Duration(grace)*time.Second {
			continue
		}
		if err := os.RemoveAll(path); err != nil {
			return err
		}
		deleted = true
	}
	if deleted {
		return syncDirectory(deployRoot)
	}
	return nil
}
