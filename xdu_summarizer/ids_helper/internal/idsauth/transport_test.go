package idsauth

import (
	"io"
	"net/http"
	"net/http/cookiejar"
	"os"
	"testing"

	utls "github.com/refraction-networking/utls"
)

type recordingTransport struct {
	called bool
}

func (transport *recordingTransport) RoundTrip(
	*http.Request) (*http.Response, error) {
	transport.called = true
	return &http.Response{
		StatusCode: http.StatusNoContent,
		Body:       io.NopCloser(http.NoBody),
	}, nil
}

func TestNewChromiumTransportDefaults(t *testing.T) {
	tr := NewChromiumTransport()
	if tr == nil {
		t.Fatal("NewChromiumTransport should return *http.Transport")
	}
	if tr.DialTLSContext == nil {
		t.Fatal("expected a DialTLSContext")
	}
	if tr.ForceAttemptHTTP2 {
		t.Fatal("expected HTTP/2 to be disabled")
	}
	tr.Proxy = http.ProxyURL(nil) // should be configurable
}

func TestNewChromiumTransportCustomProfile(t *testing.T) {
	if got := NewChromiumTransport(utls.HelloChrome_120); got == nil {
		t.Fatal("custom profile should build")
	}
}

func TestNewChromiumClient(t *testing.T) {
	jar, err := cookiejar.New(nil)
	if err != nil {
		t.Fatal(err)
	}
	hc := NewChromiumClient(jar)
	if hc == nil || hc.Jar == nil {
		t.Fatal("NewChromiumClient should set a jar and transport")
	}
	if hc.Transport == nil {
		t.Fatal("expected a transport")
	}
	// NewClient must force redirects off so the module can follow CAS hops.
	_ = NewClient(hc)
	if hc.CheckRedirect == nil {
		t.Fatal("NewClient should set CheckRedirect")
	}
}

func TestHostRoutingTransportUsesChromiumOnlyForIDS(t *testing.T) {
	for _, test := range []struct {
		name         string
		url          string
		wantChromium bool
	}{
		{name: "IDS", url: "https://ids.xidian.edu.cn/authserver/login", wantChromium: true},
		{name: "business callback", url: "https://learning.xidian.edu.cn/login/tologin"},
	} {
		t.Run(test.name, func(t *testing.T) {
			chromium := &recordingTransport{}
			standard := &recordingTransport{}
			transport := hostRoutingTransport{
				chromium: chromium, standard: standard,
			}
			request, err := http.NewRequest(http.MethodGet, test.url, nil)
			if err != nil {
				t.Fatal(err)
			}
			response, err := transport.RoundTrip(request)
			if err != nil {
				t.Fatal(err)
			}
			response.Body.Close()
			if chromium.called != test.wantChromium ||
				standard.called == test.wantChromium {
				t.Fatalf("routing for %s: chromium=%v standard=%v",
					test.url, chromium.called, standard.called)
			}
		})
	}
}

// TestChromiumTransportLive verifies the Chromium-fingerprint transport can
// reach the IDS server. It only runs when IDS_LIVE=1 is set, so `go test`
// stays offline.
func TestChromiumTransportLive(t *testing.T) {
	if os.Getenv("IDS_LIVE") != "1" {
		t.Skip("set IDS_LIVE=1 to run the live integration test against ids.xidian.edu.cn")
	}
	jar, err := cookiejar.New(nil)
	if err != nil {
		t.Fatal(err)
	}
	client := NewChromiumClient(jar)
	req, err := http.NewRequest(http.MethodGet, "https://ids.xidian.edu.cn/authserver/login", nil)
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("User-Agent", defaultUserAgent)
	resp, err := client.Do(req)
	if err != nil {
		t.Fatalf("IDS reachable? %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("expected 200 from ids login page, got %d", resp.StatusCode)
	}
}
