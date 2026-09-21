from __future__ import annotations

from agent_runner._redact import redact_secrets

R = "<redacted>"


# --- leaks that MUST be masked (audit blocker/important inputs) ---
def test_env_assignment_password_should_be_masked_when_invoked():
    subject = "PGPASSWORD=Sup3rS3cret psql -h db"

    out = redact_secrets(subject)

    assert "Sup3rS3cret" not in out and R in out


def test_aws_secret_env_should_be_masked_when_invoked():
    subject = "AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRf claude"

    out = redact_secrets(subject)

    assert "wJalrXUtnFEMI" not in out and R in out


def test_long_flag_value_should_be_masked_when_invoked():
    out = redact_secrets("x --api-key sk-foobar1234567890ABCD y")

    actual = out

    assert actual == f"x --api-key {R} y"


def test_client_secret_flag_should_be_masked_when_invoked():
    subject = "deploy --client-secret 7f3c9a1e8b5d4f2a0c6e9d8b7a4f1e3c"

    out = redact_secrets(subject)

    assert "7f3c9a1e8b5d4f2a0c6e9d8b7a4f1e3c" not in out and R in out


def test_header_name_whole_value_should_be_masked_when_invoked():
    out = redact_secrets("X-Api-Key: AbCd1234SecretValueXYZ longtail")

    actual = "AbCd1234SecretValueXYZ"

    assert actual not in out


def test_authorization_scheme_token_should_be_masked_when_invoked():
    subject = "Authorization: Negotiate YIIZkwlongbase64tokenABCDEF123456"

    out = redact_secrets(subject)

    assert "YIIZkwlongbase64tokenABCDEF123456" not in out and R in out


def test_bearer_token_should_be_masked_when_invoked():
    subject = "got Bearer sk-ant-api03-LONGSECRETvalue000111"

    out = redact_secrets(subject)

    assert "sk-ant-api03-LONGSECRETvalue000111" not in out and R in out


def test_url_userinfo_user_pass_should_be_masked_when_invoked():
    subject = "git clone https://x-token:ghp_aaaaaaaaaaaaaaaaaaaa@github.com/o/r"

    out = redact_secrets(subject)

    assert "ghp_aaaaaaaaaaaaaaaaaaaa" not in out and "@github.com/o/r" in out


def test_url_empty_username_should_be_masked_when_invoked():
    subject = "redis-cli -u redis://:s3cr3tPssw0rd@cache:6379/0 PING"

    out = redact_secrets(subject)

    assert "s3cr3tPssw0rd" not in out and "@cache:6379/0" in out


def test_url_query_token_should_be_masked_when_invoked():
    subject = "curl https://api/v1/run?access_token=A1b2C3d4E5f6G7h8I9j0"

    out = redact_secrets(subject)

    assert "A1b2C3d4E5f6G7h8I9j0" not in out and R in out


def test_jwt_should_be_masked_when_invoked():
    subject = "expired: eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjMifQ.AbCsigVALUE"

    out = redact_secrets(subject)

    assert "eyJhbGciOiJIUzI1NiJ9" not in out and R in out


def test_pem_private_key_should_be_masked_when_invoked():
    pem = (
        "-----BEGIN OPENSSH PRIVATE KEY-----\n"
        "b3BlbnNzaEKEYMATERIAL\n"
        "-----END OPENSSH PRIVATE KEY-----"
    )

    actual = "b3BlbnNzaEKEYMATERIAL"

    assert actual not in redact_secrets(pem)


def test_known_token_prefixes_should_be_masked_when_invoked():
    tokens = [
        "AKIAIOSFODNN7EXAMPLE",
        "AIzaSyD3aBcDeFgHiJkLmNoPqRsTuVwXyZ012345",
        "sk_" + "live_51HabcDEFghij0123456789ABCD",
        "npm_aBcDeF0123456789aBcDeF0123456789",
    ]

    leaked = [tok for tok in tokens if tok in redact_secrets(f"err token {tok} more")]

    assert leaked == []


def test_redact_secrets_should_be_idempotent_when_invoked():
    once = redact_secrets("x --api-key sk-foobar1234567890ABCD https://:p@h@x")

    actual = redact_secrets(once)

    assert actual == once


# --- benign text that must pass through UNCHANGED (over-redaction regressions) ---
def test_short_host_flag_lowercase_h_should_not_be_redacted_when_invoked():
    # the v1 bug: case-insensitive -H ate -h <host>
    out = redact_secrets("psql -h db.internal -U app")

    actual = out

    assert actual == "psql -h db.internal -U app"


def test_compiler_flag_h_should_not_be_redacted_when_invoked():
    out = redact_secrets("gcc -H foo.c")

    actual = out

    assert actual == "gcc -H foo.c"


def test_prefix_like_filenames_should_not_be_redacted_when_invoked():
    # the v1 bug: prefix pass with no length anchor ate sk-report.md
    lines = [
        "./build.sh --report sk-report.md",
        "deploy --host sk-prod.internal",
        "AKIAtlas mapping tool",
        "open AIza-notes.txt",
        "glpat-changelog.md",
    ]

    changed = [s for s in lines if redact_secrets(s) != s]

    assert changed == []


def test_short_u_flag_should_mask_basic_auth_when_invoked():
    # curl/wget -u user:pass — short HTTP-basic flag.
    admin_out = redact_secrets("curl -u admin:S3cr3tPassw0rd https://api")

    wget_out = redact_secrets("wget -u user:pass http://x")

    assert admin_out == f"curl -u {R} https://api"

    assert wget_out == f"wget -u {R} http://x"


def test_short_u_flag_should_not_be_over_redacted_when_invoked():
    # -U (psql username, uppercase) must NOT be masked (case-sensitive rule).
    upper_out = redact_secrets("psql -U app -h db")

    # -u without a colon (sort -u file, curl -u username) is not a stored secret.
    sort_out = redact_secrets("sort -u file.txt")
    curl_out = redact_secrets("curl -u username https://x")

    assert upper_out == "psql -U app -h db"
    assert sort_out == "sort -u file.txt"

    assert curl_out == "curl -u username https://x"


def test_auth_prose_without_token_should_not_be_redacted_when_invoked():
    out = redact_secrets("Basic auth disabled; Bearer flow off")

    actual = out

    assert actual == "Basic auth disabled; Bearer flow off"


def test_misc_benign_command_lines_should_not_be_redacted_when_invoked():
    lines = [
        "/bin/bash -c source /home/u/.claude/shell-snapshots/snapshot-bash-abc.sh && sh",
        "./build.sh smart --jobs 4",
        "",
    ]

    changed = [s for s in lines if redact_secrets(s) != s]

    assert changed == []
