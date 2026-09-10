// Package idsauth is a standalone, reusable client for the Xidian University
// unified identity authentication system (IDS / 统一身份认证). It implements:
//
//   - the CAS username/password login (with the school's AES-CBC password
//     scheme),
//   - the slider CAPTCHA solver,
//   - the browser-fingerprint registration,
//   - second-factor (MFA / reAuth) verification for SMS, Enterprise WeChat and
//     Email dynamic codes,
//   - following the cross-domain CAS redirect chain back to a business system.
//
// The second-factor path is only entered when the server actually redirects to
// /authserver/reAuthCheck/reAuthLoginView.do; a plain login never touches it.
//
// It depends only on the standard library plus golang.org/x/net for HTML
// parsing. The caller supplies an *http.Client (with a cookie jar, redirects
// disabled) via NewClient.
package idsauth
