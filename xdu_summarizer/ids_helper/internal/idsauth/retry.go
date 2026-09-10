package idsauth

// Transient-network-error retry. The IDS flow spans several hosts (ids, the
// CAS service, chaoxing) that can drop a connection or time out intermittently;
// a transient EOF/reset/timeout should be retried with a short backoff rather
// than aborting the whole login.

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net"
	"syscall"
	"time"
)

const (
	maxTransientRetries = 15
	retryBackoffStart   = 10
	retryBackoffBase    = 200 * time.Millisecond
)

type transientHTTPStatusError struct {
	status int
}

func (err transientHTTPStatusError) Error() string {
	return fmt.Sprintf("transient HTTP status %d", err.status)
}

// isTransient reports whether err is a transient network failure worth
// retrying (connection closed/reset, EOF, timeout), as opposed to a protocol
// or HTTP-level error.
func isTransient(err error) bool {
	if err == nil {
		return false
	}
	if errors.Is(err, io.EOF) || errors.Is(err, io.ErrUnexpectedEOF) {
		return true
	}
	var statusErr transientHTTPStatusError
	if errors.As(err, &statusErr) {
		return true
	}
	var netErr net.Error
	if errors.As(err, &netErr) && netErr.Timeout() {
		return true
	}
	var opErr *net.OpError
	if errors.As(err, &opErr) {
		var errno syscall.Errno
		if errors.As(opErr, &errno) {
			switch errno {
			case syscall.ECONNRESET, syscall.ECONNREFUSED,
				syscall.ECONNABORTED, syscall.EPIPE:
				return true
			}
		}
	}
	return false
}

// retryRetryable runs fn, retrying transient errors up to maxTransientRetries
// times with a short backoff.
func retryTransient(ctx context.Context, fn func() error) error {
	var lastErr error
	for i := 0; i <= maxTransientRetries; i++ {
		err := fn()
		if err == nil {
			return nil
		}
		lastErr = err
		if !isTransient(err) {
			return err
		}
		if i == maxTransientRetries {
			return lastErr
		}
		delay := transientRetryDelay(i + 1)
		if delay == 0 {
			continue
		}
		timer := time.NewTimer(delay)
		select {
		case <-ctx.Done():
			timer.Stop()
			return ctx.Err()
		case <-timer.C:
		}
	}
	return lastErr
}

func transientRetryDelay(retry int) time.Duration {
	if retry < retryBackoffStart {
		return 0
	}
	return retryBackoffBase << (retry - retryBackoffStart)
}
