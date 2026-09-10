package idsauth

// Error types surfaced by the IDS auth flow.

// ReAuthRequiredError means the server asked for a second factor but no
// ReAuthHandler was provided.
type ReAuthRequiredError struct{}

func (*ReAuthRequiredError) Error() string {
	return "登录需要二次认证，请打开应用后重试"
}

// ReAuthCodeRejectedError means the submitted code was wrong/rejected.
type ReAuthCodeRejectedError struct{ Message string }

func (e *ReAuthCodeRejectedError) Error() string { return e.Message }

// ReAuthExpiredError means the second-factor challenge expired.
type ReAuthExpiredError struct{ Message string }

func (e *ReAuthExpiredError) Error() string { return e.Message }

// ReAuthCancelledError means the user cancelled the second factor.
type ReAuthCancelledError struct{}

func (*ReAuthCancelledError) Error() string { return "已取消二次认证" }

// PasswordWrongError means the IDS username/password was rejected.
type PasswordWrongError struct{ Message string }

func (e *PasswordWrongError) Error() string { return e.Message }

// LoginFailedError is a generic IDS login failure.
type LoginFailedError struct{ Message string }

func (e *LoginFailedError) Error() string { return e.Message }
