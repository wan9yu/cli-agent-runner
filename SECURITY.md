# Security policy

## Supported versions

The latest tagged release. We do not back-port security fixes to older versions.

## Reporting a vulnerability

Please report security issues privately via GitHub Security Advisories:
<https://github.com/wan9yu/cli-agent-runner/security/advisories/new>

If GitHub Security Advisories is not workable for you, email
`wangyu@go2imagination.com` with the subject line `agent-runner security`.

Please **do not** open a public issue for security problems.

## Response timeline

- Acknowledgement within 3 business days.
- An initial assessment within 7 business days.
- A fix and coordinated disclosure timeline once the scope is understood.

## Scope

In scope: the `agent_runner` Python package, its CLI, its bundled systemd
unit templates, and the CI / release workflows.

Out of scope: vulnerabilities in dependencies (psutil, etc.) — please
report those upstream. Misconfiguration of a downstream deployment is also
out of scope unless agent-runner's defaults caused it.
