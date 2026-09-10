package idsauth

// Chromium TLS-fingerprint transport.
//
// The IDS server fronts its login with a WAF that fingerprint-checks the TLS
// ClientHello (JA3/JA4) and drops non-browser clients (this is why plain
// net/http, curl, openssl and cffi-style impersonation fail the handshake).
// This transport impersonates a Chromium TLS handshake via uTLS so the IDS
// WAF accepts the connection.
//
// It forces HTTP/1.1 because Go's standard HTTP/2 stack cannot run over a
// uTLS connection (a real Chromium advertises h2; if we did too, the server
// would choose h2 and the stdlib h1 client would break with a protocol
// error). HTTP/1.1 + the Chromium TLS fingerprint is what the IDS WAF
// accepts (verified live).

import (
	"context"
	"net"
	"net/http"
	"net/url"
	"time"

	utls "github.com/refraction-networking/utls"
)

type hostRoutingTransport struct {
	chromium http.RoundTripper
	standard http.RoundTripper
}

func (transport hostRoutingTransport) RoundTrip(
	request *http.Request) (*http.Response, error) {
	if request.URL.Hostname() == "ids.xidian.edu.cn" {
		return transport.chromium.RoundTrip(request)
	}
	return transport.standard.RoundTrip(request)
}

// DefaultChromiumProfile is the Chromium ClientHelloID impersonated by
// NewChromiumTransport when no profile is supplied.
var DefaultChromiumProfile = utls.HelloChrome_131

// NewChromiumTransport returns an *http.Transport that presents a Chromium
// TLS fingerprint to the peer (bypassing the IDS WAF). It always negotiates
// HTTP/1.1.
//
// An optional [utls.ClientHelloID] profile may be passed to choose another
// Chromium version (e.g. utls.HelloChrome_120). The default is a common,
// current Chromium fingerprint.
//
// The returned *http.Transport is fully configurable — set its Proxy field
// (e.g. http.ProxyURL(...)) to route through a proxy before handing it to an
// http.Client.
func NewChromiumTransport(profiles ...utls.ClientHelloID) *http.Transport {
	hello := DefaultChromiumProfile
	if len(profiles) > 0 && profiles[0].Client != "" {
		hello = profiles[0]
	}
	dialer := &net.Dialer{Timeout: 15 * time.Second, KeepAlive: 30 * time.Second}
	return &http.Transport{
		ForceAttemptHTTP2: false,
		DialTLSContext: func(ctx context.Context, network, addr string) (net.Conn, error) {
			host, _, err := net.SplitHostPort(addr)
			if err != nil {
				host = addr
			}
			raw, err := dialer.DialContext(ctx, network, addr)
			if err != nil {
				return nil, err
			}
			// Chronicle the Chromium ClientHello.
			uconn := utls.UClient(raw, &utls.Config{ServerName: host}, hello)
			if err := uconn.BuildHandshakeState(); err != nil {
				raw.Close()
				return nil, err
			}
			// The stdlib http.Client can only run HTTP/1.1 here, so advertise
			// only h1 in ALPN (otherwise the server picks h2 and breaks).
			uconn.HandshakeState.Hello.AlpnProtocols = []string{"http/1.1"}
			if err := uconn.MarshalClientHello(); err != nil {
				raw.Close()
				return nil, err
			}
			if err := uconn.HandshakeContext(ctx); err != nil {
				raw.Close()
				return nil, err
			}
			return uconn, nil
		},
	}
}

// NewChromiumClient returns an *http.Client whose transport presents a
// Chromium TLS fingerprint (see NewChromiumTransport) and which persists
// cookies in [jar]. It does not set the redirect policy — pass it to
// NewClient, which disables redirect-following so the module can follow the
// CAS redirect chain itself.
//
// An optional proxy URL (e.g. "http://127.0.0.1:7890") may be passed to
// route requests through a proxy.
// DefaultChromiumClientTimeout bounds the whole request so a slow/stuck peer
// cannot block the login flow forever. It is kept modest so a transient hang
// is detected quickly and the retry logic in retry.go can recover it fast.
const DefaultChromiumClientTimeout = 20 * time.Second

func NewChromiumClient(jar http.CookieJar, proxyURL ...string) *http.Client {
	chromium := NewChromiumTransport()
	standard := http.DefaultTransport.(*http.Transport).Clone()
	if len(proxyURL) > 0 && proxyURL[0] != "" {
		if u, err := url.Parse(proxyURL[0]); err == nil {
			chromium.Proxy = http.ProxyURL(u)
			standard.Proxy = http.ProxyURL(u)
		}
	}
	return &http.Client{
		Transport: hostRoutingTransport{chromium: chromium, standard: standard},
		Jar:       jar, Timeout: DefaultChromiumClientTimeout,
	}
}
