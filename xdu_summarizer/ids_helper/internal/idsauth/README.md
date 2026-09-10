# ids-auth

A standalone, reusable **Go** client for the Xidian University unified identity
authentication system (IDS / 统一身份认证). It implements:

- the CAS **username/password login** (with the school's AES-CBC password
  scheme),
- the **slider CAPTCHA** solver (normalized cross-correlation + human-like
  finger trace),
- the **browser-fingerprint** registration,
- the **second-factor (MFA / reAuth)** verification for **SMS**, **Enterprise
  WeChat** and **Email** dynamic codes,
- following the cross-domain CAS redirect chain back to a business system.

It depends on the Go standard library plus `golang.org/x/net` (HTML parsing)
and `github.com/refraction-networking/utls` (Chromium TLS fingerprint for the
IDS WAF), and exposes no global state. The caller supplies an `*http.Client`
(with a cookie jar, and redirects handled by `NewClient`) to `NewClient`.

Because the IDS server fingerprint-checks TLS, use the Chromium transport
(`NewChromiumClient` / `NewChromiumTransport`) so the WAF accepts the
connection.

> The **second-factor path is only entered when the server really triggers
> 2FA** (i.e. it redirects to `/authserver/reAuthCheck/reAuthLoginView.do`).
> A plain login never touches it; if 2FA is triggered but no `ReAuthHandler`
> is supplied, `Login` returns `*ReAuthRequiredError`.

## Usage

```go
package main

import (
	"context"
	"log"
	"net/http"
	"net/http/cookiejar"

	"github.com/StellarForager/Quasar-core/idsauth"
)

func main() {
	jar, _ := cookiejar.New(nil)
	// This client impersonates a Chromium TLS handshake so the IDS WAF accepts
	// it. NewClient then disables redirect-following for the CAS flow.
	hc := idsauth.NewChromiumClient(jar)

	client := idsauth.NewClient(hc)
	client.SetRegisterBrowserFingerprint(func(ctx context.Context) error {
		// Persist/register the fingerprint once per browser/session.
		return nil
	})

	solver := idsauth.NewSliderCaptchaSolver(hc)

	// The handler is only called when the server asks for a second factor.
	reAuth := func(ctx context.Context, rc *idsauth.ReAuthClient) (string, error) {
		d, err := rc.SendCode(ctx, idsauth.Email) // sms / enterpriseWechat / email
		if err != nil {
			return "", err
		}
		log.Printf("code sent: %s %s", d.Message, d.MaskedMobile)
		code := readCodeFromUser() // your UI
		return rc.SubmitCode(ctx, idsauth.Email, code, true)
	}

	ticket, err := client.Login(context.Background(),
		"student-id", "password",
		"https://ehall.xidian.edu.cn/",
		idsauth.LoginOptions{
			SolveSliderCaptcha: solver.Solve,
			ReAuthHandler:      reAuth,
		},
	)
	if err != nil {
		log.Fatal(err)
	}
	log.Println("service URL:", ticket)
}
```

You can also skip the built-in CAPTCHA solver and pass your own
`SolveSliderCaptcha` (or a manual solver) inside `LoginOptions`.

## Two-phase second factor (resumable)

`Login` blocks until the optional second factor completes. When the code must
be entered in a **separate round-trip** (typical for an HTTP API), use the
resumable pattern: run the login up to the challenge, send the code and
**pause**; keep the `ReAuthClient` and the session cookie jar alive; resume
later with `SubmitCode`.

```go
var captured *idsauth.ReAuthClient
var sentTo string
var paused = errors.New("paused at second factor")

reAuth := func(ctx context.Context, rc *idsauth.ReAuthClient) (string, error) {
	captured = rc
	d, err := rc.SendCode(ctx, idsauth.Email) // sms / enterpriseWechat / email
	if err != nil {
		return "", err
	}
	sentTo = strings.TrimSpace(d.Message + " " + d.MaskedMobile)
	return "", paused // signal "waiting for the code"
}

_, err := client.Login(ctx, user, pass, service,
	idsauth.LoginOptions{SolveSliderCaptcha: solver.Solve, ReAuthHandler: reAuth})
if errors.Is(err, paused) && captured != nil {
	// The code was sent (see `sentTo`). The IDS session lives in the cookie jar
	// of `hc`. Cache `captured` + `hc` + the code type, then resume later:
	ticket, err := captured.SubmitCode(ctx, idsauth.Email, "<code>", true)
	// ... followRedirects(...) and collect service cookies ...
}
```

- The module does **not** tear down the session when the handler returns an
error, so the cached `ReAuthClient`/`http.Client` stay usable to resume.
- In the resume step, `SubmitCode` returns `*ReAuthCodeRejectedError` on a wrong
code (retry with the same session; the challenge is still live) and
`*ReAuthExpiredError` when the challenge has expired (discard the session and
restart).
- The quasar integration builds a retry-able, expiring session cache on top of
this (see `xidian.StartIdsLogin`/`SubmitReAuthCode`).

## Chromium TLS fingerprint (WAF bypass)

The IDS server is fronted by a WAF that fingerprint-checks the TLS ClientHello
and drops non-browser clients (plain `net/http`, `curl`, `openssl` and cffi-style
impersonation all fail the handshake). Use the Chromium transport so the WAF
accepts the connection:

```go
jar, _ := cookiejar.New(nil)
hc := idsauth.NewChromiumClient(jar)   // or: idsauth.NewChromiumTransport()
client := idsauth.NewClient(hc)
```

It impersonates a common Chromium TLS handshake via uTLS (`utls.HelloChrome_131`
by default; pass another `utls.ClientHelloID` to `NewChromiumTransport` to pick
a different version). It advertises HTTP/1.1 only, because Go's stdlib HTTP/2
cannot run over a uTLS connection — and that combination is what the IDS WAF
accepts (verified live against `ids.xidian.edu.cn`).

## API

- `NewClient(hc *http.Client) *Client`
- `NewChromiumClient(jar http.CookieJar) *http.Client`
- `NewChromiumTransport(profiles ...utls.ClientHelloID) http.RoundTripper`
- `(*Client) Login(ctx, username, password, service, LoginOptions) (string, error)`
- `(*Client) CheckAndLogin(ctx, username, password, target, LoginOptions) (string, error)`
- `(*Client) FollowRedirects(ctx, initialLocation, FollowOptions) (string, error)`
- `NewReAuthClient(...) *ReAuthClient` → `SendCode` / `SubmitCode`
- `NewSliderCaptchaSolver(hc) *SliderCaptchaSolver` → `Solve`
- `EncryptLegacyPassword`, `SignSliderCaptcha`, `GenerateBrowserFingerprint`
- Constants/helpers: `Sms`, `EnterpriseWechat`, `Email`, `ParseCodeDelivery`,
  `ParseReAuthSubmit`, `IsReAuthLocation`.

## Second-factor channels

| CodeType              | reAuthType | authCodeTypeName          |
|-----------------------|-----------:|---------------------------|
| `Sms`                 | `3`        | `reAuthDynamicCodeType`   |
| `EnterpriseWechat`    | `4`        | `reAuthWChatDynamicCodeType` |
| `Email`               | `11`       | `reAuthEmailDynamicCodeType` |

The Email channel is the one that appears under the IDS “more” (更多) dropdown
and when the client presents an English locale/UA.

## Notes

- The IDS server fingerprint-checks TLS. Always drive it with
  `NewChromiumClient` / `NewChromiumTransport`; a plain Go `http.Client` will
  be rejected by the WAF.
- No credentials are stored by this module; the caller is responsible for
  persisting them and the browser fingerprint (see `GenerateBrowserFingerprint`).
