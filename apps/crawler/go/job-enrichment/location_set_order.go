package enrichment

// The previous resolver keeps the first equal-rank/population context in its
// CPython 3.13 integer set. Preserve that observable tie behavior for positive
// GeoNames IDs, independent of Go's randomized map iteration. This models the
// documented probing, insertion and resize order in CPython setobject.c; no
// deletion or arbitrary-object hashing is needed for location context sets.
func locationSetOrder(input []int64) []int64 {
	slots := make([]int64, 8)
	used := 0
	insert := func(table []int64, id int64) bool {
		mask := uint64(len(table) - 1)
		hash := uint64(id) % ((uint64(1) << 61) - 1)
		index, perturb := hash&mask, hash
		for {
			probes := uint64(0)
			if index+9 <= mask {
				probes = 9
			}
			for p := uint64(0); p <= probes; p++ {
				if table[index+p] == id {
					return false
				}
				if table[index+p] == 0 {
					table[index+p] = id
					return true
				}
			}
			perturb >>= 5
			index = (index*5 + 1 + perturb) & mask
		}
	}
	for _, id := range input {
		if id <= 0 || !insert(slots, id) {
			continue
		}
		used++
		if used*5 < (len(slots)-1)*3 {
			continue
		}
		min := used * 4
		if used > 50000 {
			min = used * 2
		}
		size := 8
		for size <= min {
			size *= 2
		}
		replacement := make([]int64, size)
		for _, old := range slots {
			if old != 0 {
				insert(replacement, old)
			}
		}
		slots = replacement
	}
	out := []int64{}
	for _, id := range slots {
		if id != 0 {
			out = append(out, id)
		}
	}
	return out
}
