package worker

// A failed RSS stream may retain only complete original 200-job batches.
// The incomplete tail never grants absence/finalization authority.
type rssStreamPrefixError struct{ cause error }

func (e *rssStreamPrefixError) Error() string { return "RSS stream failed after a validated prefix" }
func (e *rssStreamPrefixError) Unwrap() error { return e.cause }

func rssValidatedPrefix(found RichDiscovery, err error) (RichDiscovery, error) {
	count := len(found.Jobs) / 200 * 200
	if count == 0 {
		return RichDiscovery{}, err
	}
	found.Jobs = found.Jobs[:count]
	found.Truncated = false
	return found, &rssStreamPrefixError{cause: err}
}
