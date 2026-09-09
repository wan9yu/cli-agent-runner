from __future__ import annotations

from agent_runner._redact import redact_secrets

R = "<redacted>"


# --- leaks that MUST be masked (audit blocker/important inputs) ---
def test_env_assignment_password_should_be_masked():
    out = redact_secrets("PGPASSWORD=Sup3rS3cret psql -h db")

    assert "Sup3rS3cret" not in out and R in out


def test_aws_secret_env_should_be_masked():
    out = redact_secrets("AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRf claude")

    assert "wJalrXUtnFEMI" not in out and R in out


def test_long_flag_value_should_be_masked():
    out = redact_secrets("x --api-key sk-foobar1234567890ABCD y")

    assert out == f"x --api-key {R} y"


def test_client_secret_flag_should_be_masked():
    out = redact_secrets("deploy --client-secret 7f3c9a1e8b5d4f2a0c6e9d8b7a4f1e3c")

    assert "7f3c9a1e8b5d4f2a0c6e9d8b7a4f1e3c" not in out and R in out


def test_header_name_whole_value_should_be_masked():
    out = redact_secrets("X-Api-Key: AbCd1234SecretValueXYZ longtail")

    assert "AbCd1234SecretValueXYZ" not in out


def test_authorization_scheme_token_should_be_masked():
    out = redact_secrets("Authorization: Negotiate YIIZkwlongbase64tokenABCDEF123456")

    assert "YIIZkwlongbase64tokenABCDEF123456" not in out and R in out


def test_bearer_token_should_be_masked():
    out = redact_secrets("got Bearer sk-ant-api03-LONGSECRETvalue000111")

    assert "sk-ant-api03-LONGSECRETvalue000111" not in out and R in out


def test_url_userinfo_user_pass_should_be_masked():
    out = redact_secrets("git clone https://x-token:ghp_aaaaaaaaaaaaaaaaaaaa@github.com/o/r")

    assert "ghp_aaaaaaaaaaaaaaaaaaaa" not in out and "@github.com/o/r" in out


def test_url_empty_username_should_be_masked():
    out = redact_secrets("redis-cli -u redis://:s3cr3tPssw0rd@cache:6379/0 PING")

    assert "s3cr3tPssw0rd" not in out and "@cache:6379/0" in out


def test_url_query_token_should_be_masked():
    out = redact_secrets("curl https://api/v1/run?access_token=A1b2C3d4E5f6G7h8I9j0")

    assert "A1b2C3d4E5f6G7h8I9j0" not in out and R in out


def test_jwt_should_be_masked():
    out = redact_secrets("expired: eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjMifQ.AbCsigVALUE")

    assert "eyJhbGciOiJIUzI1NiJ9" not in out and R in out


def test_pem_private_key_should_be_masked():
    pem = (
        "-----BEGIN OPENSSH PRIVATE KEY-----\n"
        "b3BlbnNzaEKEYMATERIAL\n"
        "-----END OPENSSH PRIVATE KEY-----"
    )

    assert "b3BlbnNzaEKEYMATERIAL" not in redact_secrets(pem)


def test_known_token_prefixes_should_be_masked():
    tokens = [
        "AKIAIOSFODNN7EXAMPLE",
        "AIzaSyD3aBcDeFgHiJkLmNoPqRsTuVwXyZ012345",
        "sk_" + "live_51HabcDEFghij0123456789ABCD",
        "npm_aBcDeF0123456789aBcDeF0123456789",
    ]

    for tok in tokens:
        assert tok not in redact_secrets(f"err token {tok} more")


def test_redact_secrets_should_be_idempotent():
    once = redact_secrets("x --api-key sk-foobar1234567890ABCD https://:p@h@x")

    assert redact_secrets(once) == once


# --- benign text that must pass through UNCHANGED (over-redaction regressions) ---
def test_short_host_flag_lowercase_h_should_not_be_redacted():
    # the v1 bug: case-insensitive -H ate -h <host>
    out = redact_secrets("psql -h db.internal -U app")

    assert out == "psql -h db.internal -U app"


def test_compiler_flag_h_should_not_be_redacted():
    out = redact_secrets("gcc -H foo.c")

    assert out == "gcc -H foo.c"


def test_prefix_like_filenames_should_not_be_redacted():
    # the v1 bug: prefix pass with no length anchor ate sk-report.md
    lines = [
        "./build.sh --report sk-report.md",
        "deploy --host sk-prod.internal",
        "AKIAtlas mapping tool",
        "open AIza-notes.txt",
        "glpat-changelog.md",
    ]

    for s in lines:
        assert redact_secrets(s) == s


def test_short_u_flag_should_mask_basic_auth():
    # curl/wget -u user:pass — short HTTP-basic flag.
    admin_out = redact_secrets("curl -u admin:S3cr3tPassw0rd https://api")
    wget_out = redact_secrets("wget -u user:pass http://x")

    assert admin_out == f"curl -u {R} https://api"
    assert wget_out == f"wget -u {R} http://x"


def test_short_u_flag_should_not_be_over_redacted():
    # -U (psql username, uppercase) must NOT be masked (case-sensitive rule).
    upper_out = redact_secrets("psql -U app -h db")
    # -u without a colon (sort -u file, curl -u username) is not a stored secret.
    sort_out = redact_secrets("sort -u file.txt")
    curl_out = redact_secrets("curl -u username https://x")

    assert upper_out == "psql -U app -h db"
    assert sort_out == "sort -u file.txt"
    assert curl_out == "curl -u username https://x"


def test_auth_prose_without_token_should_not_be_redacted():
    out = redact_secrets("Basic auth disabled; Bearer flow off")

    assert out == "Basic auth disabled; Bearer flow off"


def test_misc_benign_command_lines_should_not_be_redacted():
    lines = [
        "/bin/bash -c source /home/u/.claude/shell-snapshots/snapshot-bash-abc.sh && sh",
        "./build.sh smart --jobs 4",
        "",
    ]

    for s in lines:
        assert redact_secrets(s) == s
