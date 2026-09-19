"""
SaveWaypoint - TLS setup.

On SteamOS the CA bundle is not where Python's default SSL context looks for it,
so every HTTPS call from a plugin backend fails with

    <urlopen error [SSL: CERTIFICATE_VERIFY_FAILED] unable to get local issuer
    certificate>

unless the trust store is pointed at explicitly. We look for certifi first, then
the usual Arch/SteamOS bundle locations.

Verification is never disabled: these connections carry an OAuth token, so an
unverified context would hand that token to anyone able to intercept the
connection.
"""

import os
import ssl

CA_PATHS = (
    "/etc/ssl/certs/ca-certificates.crt",
    "/etc/ca-certificates/extracted/tls-ca-bundle.pem",
    "/etc/pki/tls/certs/ca-bundle.crt",
    "/etc/ssl/cert.pem",
)


def _build_context():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        pass
    for path in CA_PATHS:
        if os.path.exists(path):
            try:
                return ssl.create_default_context(cafile=path)
            except Exception:
                continue
    return ssl.create_default_context()


SSL_CONTEXT = _build_context()


def describe_tls():
    """Which trust store we ended up using - surfaced in errors so a TLS failure
    is diagnosable from the plugin instead of needing a shell."""
    try:
        import certifi
        return f"certifi ({certifi.where()})"
    except Exception:
        pass
    for path in CA_PATHS:
        if os.path.exists(path):
            return path
    return "python default (no CA bundle found on disk)"
