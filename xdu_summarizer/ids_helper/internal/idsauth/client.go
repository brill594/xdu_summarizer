package idsauth

// IDS (Xidian unified authentication) CAS login client. It handles the primary
// username/password login, the slider CAPTCHA handshake, browser-fingerprint
// registration, the optional second-factor (MFA) challenge, and following the
// cross-domain CAS redirects back to a business service.
//
// The client only enters the second-factor path when the server actually
// redirects to /authserver/reAuthCheck/reAuthLoginView.do (see
// resolveReAuthIfNeeded); a plain login never touches it.

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"time"

	"golang.org/x/net/html"
)

const defaultUserAgent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"

const maxAuthRedirects = 30

// LoginOptions configures a login call.
type LoginOptions struct {
	// SolveSliderCaptcha solves the IDS slider CAPTCHA. If nil, a built-in
	// solver using the client's HTTP is used. A manual solver is useful when
	// you cannot run image matching.
	SolveSliderCaptcha func(context.Context) error
	// ReAuthHandler is invoked only when the server triggers 2FA. If 2FA is
	// triggered but this is nil, Login returns *ReAuthRequiredError.
	ReAuthHandler ReAuthHandler
	// OnProgress reports progress (0..100, message key) if non-nil.
	OnProgress func(int, string)
}

// FollowOptions configures FollowRedirects.
type FollowOptions struct {
	Service       string
	Username      string
	ReAuthHandler ReAuthHandler
}

// Client is the IDS auth client. It is safe for concurrent use but serializes
// auth flows so each caller gets its own service ticket.
type Client struct {
	http *http.Client

	registerBrowserFingerprint func(context.Context) error
	onSessionInvalid           func(context.Context) error

	mu       sync.Mutex // serializes whole login flows
	reAuthMu sync.Mutex // serializes 2FA challenges
}

// NewClient builds an IDS auth client. hc must carry a cookie jar. NewClient
// forces the client to NOT auto-follow redirects, because the module follows
// the CAS redirect chain itself (inspecting Location headers); a default
// http.Client would otherwise follow them automatically and break the flow.
//
// For the IDS WAF, build hc with NewChromiumClient or set a transport from
// NewChromiumTransport.
func NewClient(hc *http.Client) *Client {
	hc.CheckRedirect = func(req *http.Request, via []*http.Request) error {
		return http.ErrUseLastResponse
	}
	return &Client{http: hc}
}

// SetRegisterBrowserFingerprint installs a callback invoked before login to
// persist/register the browser fingerprint.
func (c *Client) SetRegisterBrowserFingerprint(fn func(context.Context) error) {
	c.registerBrowserFingerprint = fn
}

// SetOnSessionInvalid installs a callback invoked when the session becomes
// invalid (2FA cancelled / expired) so the caller can clear cookies.
func (c *Client) SetOnSessionInvalid(fn func(context.Context) error) {
	c.onSessionInvalid = fn
}

// Login performs a fresh username/password login and returns the resolved
// business service URL.
func (c *Client) Login(ctx context.Context, username, password, service string, opts LoginOptions) (string, error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.loginOnce(ctx, username, password, service, opts)
}

// CheckAndLogin reuses an existing IDS session when possible; otherwise it
// performs a fresh login using the supplied credentials and returns the
// resolved [target].
func (c *Client) CheckAndLogin(ctx context.Context, username, password, target string, opts LoginOptions) (string, error) {
	c.mu.Lock()
	defer c.mu.Unlock()

	u, err := c.loginPageURL(target)
	if err != nil {
		return "", err
	}
	resp, err := c.do(ctx, http.MethodGet, u, nil)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()
	if isRedirect(resp.StatusCode) {
		return c.completeRedirect(ctx, resp, target, username, opts)
	}
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return "", err
	}

	if continued, ok := c.submitContinueForm(ctx, body); ok {
		if isRedirect(continued.StatusCode) {
			return c.completeRedirect(ctx, continued, target, username, opts)
		}
		continued.Body.Close()
	}

	return c.loginOnce(ctx, username, password, target, opts)
}

// FollowRedirects follows the CAS redirect chain from initialLocation back to
// the final business URL, resolving any MFA challenges along the way.
func (c *Client) FollowRedirects(ctx context.Context, initialLocation string, opts FollowOptions) (string, error) {
	var currentURI *url.URL
	if u, err := url.Parse(initialLocation); err == nil && u.IsAbs() {
		currentURI = u
	} else {
		base, err := url.Parse(idsOrigin)
		if err != nil {
			return "", err
		}
		currentURI = base.ResolveReference(&url.URL{Path: initialLocation})
	}
	currentService := opts.Service
	redirectCount := 0

	for {
		if svc := c.idsLoginService(currentURI); svc != nil {
			currentService = *svc
		}
		resolved, err := c.resolveReAuthIfNeeded(ctx, currentURI, currentService, opts.Username, opts.ReAuthHandler)
		if err != nil {
			return "", err
		}
		currentURI = resolved
		if svc := c.idsLoginService(currentURI); svc != nil {
			currentService = *svc
		}

		resp, err := c.do(ctx, http.MethodGet, currentURI.String(), nil)
		if err != nil {
			return "", err
		}
		location := resp.Header.Get("Location")
		if location == "" {
			resp.Body.Close()
			return resp.Request.URL.String(), nil
		}
		ref, err := url.Parse(location)
		if err != nil {
			resp.Body.Close()
			return "", err
		}
		resp.Body.Close()

		redirectCount++
		currentURI = resp.Request.URL.ResolveReference(ref)
		if redirectCount > maxAuthRedirects {
			return "", &LoginFailedError{Message: "统一认证跳转次数超过 30 次"}
		}
	}
}

func (c *Client) loginOnce(ctx context.Context, username, password, service string, opts LoginOptions) (string, error) {
	report(opts.OnProgress, 10, "login_process.ready_page")

	u, err := c.loginPageURL(service)
	if err != nil {
		return "", err
	}
	resp, err := c.do(ctx, http.MethodGet, u, nil)
	if err != nil {
		return "", err
	}
	body, err := io.ReadAll(resp.Body)
	resp.Body.Close()
	if err != nil {
		return "", err
	}
	if isRedirect(resp.StatusCode) {
		return c.completeRedirect(ctx, resp, service, username, opts)
	}
	if resp.StatusCode == http.StatusUnauthorized {
		return "", &PasswordWrongError{Message: parsePasswordWrongMsg(string(body))}
	}

	if c.registerBrowserFingerprint != nil {
		if err := c.registerBrowserFingerprint(ctx); err != nil {
			return "", err
		}
	}

	report(opts.OnProgress, 30, "login_process.get_encrypt")
	salt, err := fieldFromHTML(string(body), "pwdEncryptSalt", "pwdEncryptSalt")
	if err != nil {
		return "", err
	}
	lt, _ := fieldFromHTML(string(body), "lt", "lt")
	execution, _ := fieldFromHTML(string(body), "execution", "execution")

	report(opts.OnProgress, 40, "login_process.ready_login")
	encPassword, err := EncryptLegacyPassword(password, salt)
	if err != nil {
		return "", err
	}
	form := url.Values{}
	form.Set("username", username)
	form.Set("password", encPassword)
	form.Set("rememberMe", "true")
	form.Set("cllt", "userNameLogin")
	form.Set("dllt", "generalLogin")
	form.Set("_eventId", "submit")
	if lt != "" {
		form.Set("lt", lt)
	}
	if execution != "" {
		form.Set("execution", execution)
	}

	report(opts.OnProgress, 45, "login_process.slider")
	// Warm up the CAPTCHA session like the web page does.
	warm, err := c.do(ctx, http.MethodGet, idsOrigin+"/authserver/common/openSliderCaptcha.htl?_="+strconv.FormatInt(time.Now().UnixMilli(), 10), nil)
	if err != nil {
		return "", err
	}
	warm.Body.Close()

	if opts.SolveSliderCaptcha != nil {
		if err := opts.SolveSliderCaptcha(ctx); err != nil {
			return "", err
		}
	} else {
		if err := NewSliderCaptchaSolver(c.http).Solve(ctx); err != nil {
			return "", err
		}
	}

	report(opts.OnProgress, 50, "login_process.ready_login")
	loginURL, err := c.loginPageURL(service)
	if err != nil {
		return "", err
	}
	postResp, err := c.do(ctx, http.MethodPost, loginURL, []byte(form.Encode()))
	if err != nil {
		return "", err
	}
	postBody, err := io.ReadAll(postResp.Body)
	postResp.Body.Close()
	if err != nil {
		return "", err
	}
	if isRedirect(postResp.StatusCode) {
		return c.completeRedirect(ctx, postResp, service, username, opts)
	}
	if postResp.StatusCode == http.StatusUnauthorized {
		return "", &PasswordWrongError{Message: parsePasswordWrongMsg(string(postBody))}
	}

	// CAS "continue" form posted back to itself.
	if continued, ok := c.submitContinueForm(ctx, postBody); ok {
		if isRedirect(continued.StatusCode) {
			return c.completeRedirect(ctx, continued, service, username, opts)
		}
		continued.Body.Close()
	}

	return "", &LoginFailedError{Message: fmt.Sprintf("登录失败，响应状态码：%d。", postResp.StatusCode)}
}

func (c *Client) completeRedirect(ctx context.Context, resp *http.Response, service, username string, opts LoginOptions) (string, error) {
	defer resp.Body.Close()
	location := resp.Header.Get("Location")
	if location == "" {
		return "", protocolErrorf("统一认证跳转响应缺少 Location")
	}
	ref, err := url.Parse(location)
	if err != nil {
		return "", err
	}
	uri := resp.Request.URL.ResolveReference(ref)
	resolved, err := c.resolveReAuthIfNeeded(ctx, uri, service, username, opts.ReAuthHandler)
	if err != nil {
		return "", err
	}
	report(opts.OnProgress, 80, "login_process.after_process")
	return resolved.String(), nil
}

// resolveReAuthIfNeeded only enters the 2FA path when uri is the IDS reAuth
// view; otherwise it returns uri unchanged.
func (c *Client) resolveReAuthIfNeeded(ctx context.Context, uri *url.URL, service, username string, handler ReAuthHandler) (*url.URL, error) {
	ok, err := IsReAuthLocation(uri.String(), "")
	if err != nil {
		return nil, err
	}
	if !ok {
		return uri, nil
	}
	c.reAuthMu.Lock()
	defer c.reAuthMu.Unlock()

	if handler == nil {
		return nil, &ReAuthRequiredError{}
	}
	svc := service
	if v := uri.Query().Get("service"); v != "" {
		svc = v
	}
	rc := NewReAuthClient(c.http, uri.String(), username, svc, c.registerBrowserFingerprint)
	resumed, err := handler(ctx, rc)
	if err != nil {
		var cancelled *ReAuthCancelledError
		var expired *ReAuthExpiredError
		if errors.As(err, &cancelled) || errors.As(err, &expired) {
			if c.onSessionInvalid != nil {
				_ = c.onSessionInvalid(ctx)
			}
		}
		return nil, err
	}
	resumedURI, err := url.Parse(resumed)
	if err != nil {
		return nil, err
	}
	return resumedURI, nil
}

func (c *Client) submitContinueForm(ctx context.Context, body []byte) (*http.Response, bool) {
	fields, ok := continueFormFields(body)
	if !ok {
		return nil, false
	}
	u := idsOrigin + "/authserver/login"
	resp, err := c.do(ctx, http.MethodPost, u, []byte(fields.Encode()))
	if err != nil {
		return nil, false
	}
	return resp, true
}

func (c *Client) loginPageURL(service string) (string, error) {
	if service == "" {
		return idsOrigin + "/authserver/login", nil
	}
	u, err := url.Parse(idsOrigin + "/authserver/login")
	if err != nil {
		return "", err
	}
	q := u.Query()
	q.Set("service", service)
	u.RawQuery = q.Encode()
	return u.String(), nil
}

func (c *Client) do(ctx context.Context, method, url string, body []byte) (*http.Response, error) {
	var resp *http.Response
	err := retryTransient(ctx, func() error {
		req, err := http.NewRequestWithContext(ctx, method, url, bytes.NewReader(body))
		if err != nil {
			return err
		}
		req.Header.Set("User-Agent", defaultUserAgent)
		req.Header.Set("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8")
		if method == http.MethodPost {
			req.Header.Set("Content-Type", "application/x-www-form-urlencoded; charset=UTF-8")
		}
		r, err := c.http.Do(req)
		if err != nil {
			return err
		}
		switch r.StatusCode {
		case http.StatusBadGateway, http.StatusServiceUnavailable,
			http.StatusGatewayTimeout:
			_, _ = io.Copy(io.Discard, r.Body)
			r.Body.Close()
			return transientHTTPStatusError{status: r.StatusCode}
		}
		resp = r
		return nil
	})
	return resp, err
}

func (c *Client) idsLoginService(u *url.URL) *string {
	if u.Host != "ids.xidian.edu.cn" || u.Path != "/authserver/login" {
		return nil
	}
	svc := u.Query().Get("service")
	if svc == "" {
		return nil
	}
	return &svc
}

func report(fn func(int, string), n int, msg string) {
	if fn != nil {
		fn(n, msg)
	}
}

func isRedirect(status int) bool {
	return status == http.StatusMovedPermanently || status == http.StatusFound
}

var passwordWrongPattern = regexp.MustCompile(`(用户名|密码).*误`)

func parsePasswordWrongMsg(body string) string {
	msg := "登录遇到问题"
	doc, err := html.Parse(strings.NewReader(body))
	if err == nil {
		if el := findElementByID(doc, "showErrorTip"); el != nil {
			if t := elementText(el); t != "" {
				msg = t
			}
		}
	}
	if passwordWrongPattern.MatchString(msg) {
		msg = "用户名或密码有误"
	}
	return msg
}

// fieldFromHTML extracts the value of an <input> element matching name or id.
func fieldFromHTML(body, name, id string) (string, error) {
	doc, err := html.Parse(strings.NewReader(body))
	if err != nil {
		return "", err
	}
	var found *string
	var walk func(*html.Node)
	walk = func(n *html.Node) {
		if found != nil {
			return
		}
		if n.Type == html.ElementNode && n.Data == "input" {
			var nm, val, idd string
			for _, a := range n.Attr {
				switch a.Key {
				case "name":
					nm = a.Val
				case "id":
					idd = a.Val
				case "value":
					val = a.Val
				}
			}
			if (nm == name || idd == id) && (nm != "" || idd != "") {
				found = &val
				return
			}
		}
		for ch := n.FirstChild; ch != nil; ch = ch.NextSibling {
			walk(ch)
		}
	}
	walk(doc)
	if found == nil {
		return "", fmt.Errorf("idsauth: 登录页缺少字段 %s", name)
	}
	return *found, nil
}

// continueFormFields extracts the fields of the CAS <form id="continue">.
func continueFormFields(body []byte) (url.Values, bool) {
	doc, err := html.Parse(bytes.NewReader(body))
	if err != nil {
		return nil, false
	}
	formEl := findElementByID(doc, "continue")
	if formEl == nil || formEl.Data != "form" {
		return nil, false
	}
	fields := url.Values{}
	var walk func(*html.Node)
	walk = func(n *html.Node) {
		if n.Type == html.ElementNode && n.Data == "input" {
			var nm, val string
			for _, a := range n.Attr {
				switch a.Key {
				case "name":
					nm = a.Val
				case "value":
					val = a.Val
				}
			}
			if nm != "" {
				fields.Set(nm, val)
			}
		}
		for ch := n.FirstChild; ch != nil; ch = ch.NextSibling {
			walk(ch)
		}
	}
	walk(formEl)
	return fields, true
}

func findElementByID(n *html.Node, id string) *html.Node {
	if n.Type == html.ElementNode {
		for _, a := range n.Attr {
			if a.Key == "id" && a.Val == id {
				return n
			}
		}
	}
	for ch := n.FirstChild; ch != nil; ch = ch.NextSibling {
		if el := findElementByID(ch, id); el != nil {
			return el
		}
	}
	return nil
}

func elementText(n *html.Node) string {
	if n == nil {
		return ""
	}
	var b strings.Builder
	var walk func(*html.Node)
	walk = func(n *html.Node) {
		if n.Type == html.TextNode {
			b.WriteString(n.Data)
		}
		for ch := n.FirstChild; ch != nil; ch = ch.NextSibling {
			walk(ch)
		}
	}
	walk(n)
	return strings.TrimSpace(b.String())
}

// CaptchaSolveFailedError reports that automated CAPTCHA solving failed.
type CaptchaSolveFailedError struct{}

func (*CaptchaSolveFailedError) Error() string { return "验证码校验失败" }
