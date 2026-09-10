package idsauth

// IDS second-factor (reAuth) protocol types and response parsers.

import (
	"fmt"
	"net/url"
	"strconv"
	"strings"
)

const idsOrigin = "https://ids.xidian.edu.cn"

// CodeType describes a second-factor channel. The ReAuthType/AuthCodeTypeName
// pairs are the exact values the IDS server expects (see `reAuth.js`).
type CodeType struct {
	ReAuthType       string
	AuthCodeTypeName string
	Name             string
}

// Supported second-factor channels.
var (
	// Sms = short-message dynamic code.
	Sms = CodeType{ReAuthType: "3", AuthCodeTypeName: "reAuthDynamicCodeType", Name: "SMS"}
	// EnterpriseWechat = 企业微信 dynamic code.
	EnterpriseWechat = CodeType{ReAuthType: "4", AuthCodeTypeName: "reAuthWChatDynamicCodeType", Name: "WeChat Work"}
	// Email = 邮箱 dynamic code (shows up under the "more" dropdown / en-UA).
	Email = CodeType{ReAuthType: "11", AuthCodeTypeName: "reAuthEmailDynamicCodeType", Name: "Email"}

	// Other channels accepted by the server (dynamic-code based) but not
	// exposed as first-class methods here.
	cpDaily   = CodeType{ReAuthType: "5", AuthCodeTypeName: "reAuthCpdailyDynamicCodeType", Name: "Cpdaily"}
	dingTalk  = CodeType{ReAuthType: "12", AuthCodeTypeName: "reAuthDingTalkDynamicCodeType", Name: "DingTalk"}
	weLink    = CodeType{ReAuthType: "13", AuthCodeTypeName: "reAuthWeLinkDynamicCodeType", Name: "WeLink"}
	weChatSvc = CodeType{ReAuthType: "15", AuthCodeTypeName: "reAuthWeChatServiceDynamicCodeType", Name: "WeChat Service"}
)

// ReAuthSubmitStatus is one of the server's stable submit result codes.
type ReAuthSubmitStatus int

const (
	ReAuthSuccess ReAuthSubmitStatus = iota
	ReAuthFailed
	ReAuthUnauthorized
)

// ReAuthSubmitResult is the parsed reAuthSubmit response.
type ReAuthSubmitResult struct {
	Status  ReAuthSubmitStatus
	Message string
}

// CodeDelivery is the parsed "send code" response.
type CodeDelivery struct {
	Message           string
	MaskedMobile      string
	RetryAfterSeconds int
	WasAlreadySent    bool
}

// IsReAuthLocation reports whether location points at the IDS reAuth (MFA)
// view. The CAS flow redirects here when the primary password login is not
// enough. baseURI may be empty (defaults to the IDS origin).
func IsReAuthLocation(location, baseURI string) (bool, error) {
	u, err := url.Parse(location)
	if err != nil {
		return false, err
	}
	if !u.IsAbs() {
		base := baseURI
		if base == "" {
			base = idsOrigin
		}
		baseU, err := url.Parse(base)
		if err != nil {
			return false, err
		}
		u = baseU.ResolveReference(u)
	}
	return u.Scheme == "https" &&
		u.Host == "ids.xidian.edu.cn" &&
		u.Path == "/authserver/reAuthCheck/reAuthLoginView.do", nil
}

// ParseReAuthSubmit parses a reAuthSubmit.do JSON body.
func ParseReAuthSubmit(m map[string]any) (ReAuthSubmitResult, error) {
	code, _ := m["code"].(string)
	message, _ := m["msg"].(string)
	if message == "" {
		message = "二次认证失败"
	}
	switch code {
	case "reAuth_success":
		return ReAuthSubmitResult{Status: ReAuthSuccess, Message: message}, nil
	case "reAuth_failed":
		return ReAuthSubmitResult{Status: ReAuthFailed, Message: message}, nil
	case "reAuth_unauthorized":
		return ReAuthSubmitResult{Status: ReAuthUnauthorized, Message: message}, nil
	}
	return ReAuthSubmitResult{}, fmt.Errorf("idsauth: 统一认证返回了未知的二次认证状态 (%q)", code)
}

// ParseCodeDelivery parses a getDynamicCodeByReauth.do JSON body.
func ParseCodeDelivery(m map[string]any) (CodeDelivery, error) {
	result, _ := m["res"].(string)
	switch result {
	case "success", "other_success", "wechat_success", "cpdaily_success", "code_time_fail":
	default:
		msg, _ := m["returnMessage"].(string)
		if msg == "" {
			msg = "验证码发送失败"
		}
		return CodeDelivery{}, fmt.Errorf("idsauth: %s", msg)
	}

	seconds := 0
	if raw, ok := m["codeTime"].(string); ok {
		if v, err := strconv.Atoi(raw); err == nil && v >= 0 {
			seconds = v
		}
	} else if raw, ok := m["codeTime"].(float64); ok {
		seconds = int(raw)
	}
	if seconds == 0 && (result == "wechat_success" || result == "cpdaily_success") {
		seconds = 120
	}

	masked := ""
	if mobile, ok := m["mobile"].(string); ok && mobile != "" {
		masked = MaskPhoneNumber(mobile)
	}
	msg, _ := m["returnMessage"].(string)
	if msg == "" {
		msg = "验证码已发送"
	}
	return CodeDelivery{
		Message:           msg,
		MaskedMobile:      masked,
		RetryAfterSeconds: seconds,
		WasAlreadySent:    result == "code_time_fail",
	}, nil
}

// MaskPhoneNumber masks a value like `138****1234` (or `****` when too short).
func MaskPhoneNumber(value string) string {
	if len(value) < 7 {
		return "****"
	}
	return value[:3] + "****" + value[len(value)-4:]
}

// ProtocolError is a user-facing IDS protocol failure.
type ProtocolError struct{ Message string }

func (e *ProtocolError) Error() string { return strings.TrimSpace(e.Message) }

func protocolErrorf(format string, args ...any) error {
	return &ProtocolError{Message: fmt.Sprintf(format, args...)}
}
