package idsauth

import (
	"context"
	"io"
	"net/http"
	"strings"
	"testing"
	"time"
)

type retryRoundTripFunc func(*http.Request) (*http.Response, error)

func (fn retryRoundTripFunc) RoundTrip(request *http.Request) (*http.Response, error) {
	return fn(request)
}

func TestRetryScheduleStartsAtTenthRetryWithExponentialBackoff(t *testing.T) {
	if maxTransientRetries != 15 {
		t.Fatalf("max retries = %d", maxTransientRetries)
	}
	for retry, want := range map[int]time.Duration{
		1: 0, 9: 0, 10: 200 * time.Millisecond,
		11: 400 * time.Millisecond, 12: 800 * time.Millisecond,
		13: 1600 * time.Millisecond, 14: 3200 * time.Millisecond,
		15: 6400 * time.Millisecond,
	} {
		if got := transientRetryDelay(retry); got != want {
			t.Fatalf("retry %d delay = %v, want %v", retry, got, want)
		}
	}
}

func TestDoRetriesGatewayFailuresButNotClientErrors(t *testing.T) {
	requests := 0
	client := NewClient(&http.Client{Transport: retryRoundTripFunc(
		func(request *http.Request) (*http.Response, error) {
			requests++
			status := http.StatusBadGateway
			if requests == 3 {
				status = http.StatusFound
			}
			return &http.Response{
				StatusCode: status,
				Body:       io.NopCloser(strings.NewReader("response")),
				Header:     make(http.Header),
				Request:    request,
			}, nil
		})})
	response, err := client.do(
		context.Background(), http.MethodGet, "https://service.test/", nil)
	if err != nil {
		t.Fatal(err)
	}
	response.Body.Close()
	if requests != 3 || response.StatusCode != http.StatusFound {
		t.Fatalf("requests = %d, status = %d", requests, response.StatusCode)
	}

	requests = 0
	client = NewClient(&http.Client{Transport: retryRoundTripFunc(
		func(request *http.Request) (*http.Response, error) {
			requests++
			return &http.Response{
				StatusCode: http.StatusBadRequest,
				Body:       io.NopCloser(strings.NewReader("bad request")),
				Header:     make(http.Header),
				Request:    request,
			}, nil
		})})
	response, err = client.do(
		context.Background(), http.MethodGet, "https://service.test/", nil)
	if err != nil {
		t.Fatal(err)
	}
	response.Body.Close()
	if requests != 1 || response.StatusCode != http.StatusBadRequest {
		t.Fatalf("client error requests = %d, status = %d",
			requests, response.StatusCode)
	}
}
