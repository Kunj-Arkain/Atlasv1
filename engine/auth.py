"""
engine.auth — JWT Authentication & API Key Middleware
======================================================
P1.2 FIX: Replace raw X-User-Id / X-Workspace-Id headers with
real token validation.

Supports (in order of preference):
  1. JWT Bearer tokens (RS256/HS256) with OIDC discovery
  2. API keys (workspace-scoped, hashed in storage)
  3. Fallback: header-based (dev/testing only, must be explicitly enabled)

ZERO required dependencies (stdlib HMAC-SHA256 for API keys).
Optional: PyJWT for RS256/OIDC. cryptography for RS256 key verification.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Set

from engine.observability import AuditLog


# ═══════════════════════════════════════════════════════════════
# AUTH RESULT
# ═══════════════════════════════════════════════════════════════

@dataclass
class AuthResult:
    """Result of authentication attempt."""
    authenticated: bool
    user_id: str = ""
    workspace_id: str = ""
    org_id: str = ""
    email: str = ""
    roles: List[str] = field(default_factory=list)
    scopes: List[str] = field(default_factory=list)
    method: str = ""          # "jwt", "api_key", "header"
    error: str = ""
    token_exp: int = 0        # Unix timestamp
    claims: Dict[str, Any] = field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════
# JWT VALIDATOR
# ═══════════════════════════════════════════════════════════════

class JWTValidator:
    """Validate JWT tokens with configurable issuers and audiences.

    Supports:
      - HS256 (shared secret — dev/testing)
      - RS256 (asymmetric — production via OIDC)
      - OIDC discovery for JWKS endpoints
      - Clock skew tolerance
      - Required claims enforcement

    Usage:
        validator = JWTValidator(
            secret="shared-secret",                  # HS256
            # OR
            jwks_url="https://auth.example.com/.well-known/jwks.json",  # RS256
            audience="agentic-engine-api",
            issuer="https://auth.example.com/",
        )
        result = validator.validate(token_string)
    """

    def __init__(self, secret: str = "",
                 jwks_url: str = "",
                 audience: str = "",
                 issuer: str = "",
                 clock_skew_s: int = 30,
                 required_claims: Optional[List[str]] = None):
        self._secret = secret or os.getenv("JWT_SECRET", "")
        self._jwks_url = jwks_url or os.getenv("JWT_JWKS_URL", "")
        self._audience = audience or os.getenv("JWT_AUDIENCE", "")
        self._issuer = issuer or os.getenv("JWT_ISSUER", "")
        self._clock_skew = clock_skew_s
        self._required_claims = required_claims or ["sub", "exp"]

    def validate(self, token: str) -> AuthResult:
        """Validate a JWT token. Returns AuthResult."""
        if not token:
            return AuthResult(authenticated=False, error="No token provided")

        # Strip "Bearer " prefix
        if token.startswith("Bearer "):
            token = token[7:]

        try:
            # Try PyJWT first (supports RS256 + full validation)
            return self._validate_pyjwt(token)
        except ImportError:
            pass

        # Fallback: stdlib HS256 only
        return self._validate_stdlib(token)

    def _validate_pyjwt(self, token: str) -> AuthResult:
        """Validate using PyJWT library (RS256/HS256)."""
        import jwt  # PyJWT

        decode_opts: Dict[str, Any] = {
            "algorithms": ["RS256", "HS256"],
            "leeway": self._clock_skew,
        }
        if self._audience:
            decode_opts["audience"] = self._audience
        if self._issuer:
            decode_opts["issuer"] = self._issuer

        if self._secret:
            decode_opts["key"] = self._secret
        elif self._jwks_url:
            jwks_client = jwt.PyJWKClient(self._jwks_url)
            signing_key = jwks_client.get_signing_key_from_jwt(token)
            decode_opts["key"] = signing_key.key
        else:
            return AuthResult(authenticated=False,
                              error="No JWT secret or JWKS URL configured")

        try:
            claims = jwt.decode(token, **decode_opts)
        except jwt.ExpiredSignatureError:
            return AuthResult(authenticated=False, error="Token expired")
        except jwt.InvalidAudienceError:
            return AuthResult(authenticated=False, error="Invalid audience")
        except jwt.InvalidIssuerError:
            return AuthResult(authenticated=False, error="Invalid issuer")
        except jwt.InvalidTokenError as e:
            return AuthResult(authenticated=False, error=f"Invalid token: {e}")

        # Check required claims
        for claim in self._required_claims:
            if claim not in claims:
                return AuthResult(authenticated=False,
                                  error=f"Missing required claim: {claim}")

        return AuthResult(
            authenticated=True,
            user_id=claims.get("sub", ""),
            workspace_id=claims.get("workspace_id", claims.get("wid", "")),
            org_id=claims.get("org_id", claims.get("oid", "")),
            email=claims.get("email", ""),
            roles=claims.get("roles", []),
            scopes=claims.get("scope", "").split() if isinstance(claims.get("scope"), str) else claims.get("scopes", []),
            method="jwt",
            token_exp=claims.get("exp", 0),
            claims=claims,
        )

    def _validate_stdlib(self, token: str) -> AuthResult:
        """Validate HS256 JWT using stdlib only (no PyJWT)."""
        if not self._secret:
            return AuthResult(authenticated=False,
                              error="No JWT secret configured (HS256 stdlib mode)")

        parts = token.split(".")
        if len(parts) != 3:
            return AuthResult(authenticated=False, error="Malformed JWT")

        header_b64, payload_b64, signature_b64 = parts

        # Verify signature (HS256)
        signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
        expected_sig = hmac.new(
            self._secret.encode("utf-8"), signing_input, hashlib.sha256
        ).digest()
        actual_sig = self._b64url_decode(signature_b64)

        if not hmac.compare_digest(expected_sig, actual_sig):
            return AuthResult(authenticated=False, error="Invalid signature")

        # Decode payload
        try:
            payload = json.loads(self._b64url_decode(payload_b64))
        except (json.JSONDecodeError, ValueError):
            return AuthResult(authenticated=False, error="Invalid payload")

        # Check header alg
        try:
            header = json.loads(self._b64url_decode(header_b64))
            if header.get("alg") != "HS256":
                return AuthResult(authenticated=False,
                                  error=f"Unsupported algorithm: {header.get('alg')} (stdlib supports HS256 only)")
        except (json.JSONDecodeError, ValueError):
            return AuthResult(authenticated=False, error="Invalid header")

        # Check expiration
        exp = payload.get("exp", 0)
        if exp and time.time() > exp + self._clock_skew:
            return AuthResult(authenticated=False, error="Token expired")

        # Check issuer
        if self._issuer and payload.get("iss") != self._issuer:
            return AuthResult(authenticated=False, error="Invalid issuer")

        # Check audience
        if self._audience:
            aud = payload.get("aud", "")
            if isinstance(aud, list):
                if self._audience not in aud:
                    return AuthResult(authenticated=False, error="Invalid audience")
            elif aud != self._audience:
                return AuthResult(authenticated=False, error="Invalid audience")

        # Required claims
        for claim in self._required_claims:
            if claim not in payload:
                return AuthResult(authenticated=False,
                                  error=f"Missing required claim: {claim}")

        return AuthResult(
            authenticated=True,
            user_id=payload.get("sub", ""),
            workspace_id=payload.get("workspace_id", payload.get("wid", "")),
            org_id=payload.get("org_id", payload.get("oid", "")),
            email=payload.get("email", ""),
            roles=payload.get("roles", []),
            scopes=payload.get("scope", "").split() if isinstance(payload.get("scope"), str) else payload.get("scopes", []),
            method="jwt",
            token_exp=exp,
            claims=payload,
        )

    @staticmethod
    def _b64url_decode(s: str) -> bytes:
        s += "=" * (4 - len(s) % 4)
        return base64.urlsafe_b64decode(s)


# ═══════════════════════════════════════════════════════════════
# API KEY AUTH
# ═══════════════════════════════════════════════════════════════

@dataclass
class APIKeyRecord:
    key_id: str
    key_hash: str              # SHA-256 of the key
    workspace_id: str
    user_id: str
    scopes: List[str] = field(default_factory=list)
    created_at: str = ""
    expires_at: str = ""       # "" = never
    revoked: bool = False


class APIKeyAuth:
    """Workspace-scoped API key authentication.

    Keys are stored as SHA-256 hashes — plaintext never persisted.

    Usage:
        auth = APIKeyAuth()
        key_id, raw_key = auth.create_key("ws1", "u1", scopes=["pipeline.run"])
        result = auth.validate(raw_key)
    """

    def __init__(self):
        self._keys: Dict[str, APIKeyRecord] = {}  # key_hash → record

    def create_key(self, workspace_id: str, user_id: str,
                   scopes: Optional[List[str]] = None,
                   expires_at: str = "") -> tuple:
        """Create a new API key. Returns (key_id, raw_key).

        The raw_key is returned ONCE — it is not stored.
        """
        key_id = f"ak_{uuid.uuid4().hex[:16]}"
        raw_key = f"ae_{uuid.uuid4().hex}"
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

        record = APIKeyRecord(
            key_id=key_id, key_hash=key_hash,
            workspace_id=workspace_id, user_id=user_id,
            scopes=scopes or [],
            created_at=datetime.now(timezone.utc).isoformat(),
            expires_at=expires_at,
        )
        self._keys[key_hash] = record
        return key_id, raw_key

    def validate(self, raw_key: str) -> AuthResult:
        """Validate an API key."""
        if not raw_key:
            return AuthResult(authenticated=False, error="No API key provided")

        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        record = self._keys.get(key_hash)

        if not record:
            return AuthResult(authenticated=False, error="Invalid API key")

        if record.revoked:
            return AuthResult(authenticated=False, error="API key revoked")

        if record.expires_at:
            now = datetime.now(timezone.utc).isoformat()
            if now > record.expires_at:
                return AuthResult(authenticated=False, error="API key expired")

        return AuthResult(
            authenticated=True,
            user_id=record.user_id,
            workspace_id=record.workspace_id,
            scopes=record.scopes,
            method="api_key",
        )

    def revoke(self, key_id: str) -> bool:
        for record in self._keys.values():
            if record.key_id == key_id:
                record.revoked = True
                return True
        return False


# ═══════════════════════════════════════════════════════════════
# AUTH MIDDLEWARE
# ═══════════════════════════════════════════════════════════════

class AuthMiddleware:
    """Unified authentication middleware.

    Tries authentication methods in order:
      1. Authorization: Bearer <jwt>
      2. X-Api-Key: <key>
      3. X-User-Id + X-Workspace-Id headers (dev mode only)

    Usage:
        middleware = AuthMiddleware(
            jwt_validator=JWTValidator(secret="..."),
            api_key_auth=APIKeyAuth(),
            allow_header_auth=False,  # DISABLE in production
        )
        result = middleware.authenticate(headers)
    """

    def __init__(self, jwt_validator: Optional[JWTValidator] = None,
                 api_key_auth: Optional[APIKeyAuth] = None,
                 allow_header_auth: bool = False,
                 audit: Optional[AuditLog] = None):
        self._jwt = jwt_validator
        self._api_key = api_key_auth
        self._allow_header_auth = allow_header_auth
        self._audit = audit or AuditLog.noop()

    def authenticate(self, headers: Dict[str, str]) -> AuthResult:
        """Authenticate a request from its headers."""

        # 1. JWT Bearer token
        auth_header = headers.get("Authorization", headers.get("authorization", ""))
        if auth_header.startswith("Bearer ") and self._jwt:
            result = self._jwt.validate(auth_header)
            self._log_auth(result)
            return result

        # 2. API key
        api_key = headers.get("X-Api-Key", headers.get("x-api-key", ""))
        if api_key and self._api_key:
            result = self._api_key.validate(api_key)
            self._log_auth(result)
            return result

        # 3. Header-based (dev mode only)
        if self._allow_header_auth:
            user_id = headers.get("X-User-Id", headers.get("x-user-id", ""))
            workspace_id = headers.get("X-Workspace-Id",
                                        headers.get("x-workspace-id", ""))
            if user_id:
                result = AuthResult(
                    authenticated=True,
                    user_id=user_id,
                    workspace_id=workspace_id,
                    method="header",
                )
                self._log_auth(result)
                return result

        return AuthResult(authenticated=False,
                          error="No valid authentication credentials provided")

    def _log_auth(self, result: AuthResult):
        action = "auth.success" if result.authenticated else "auth.failure"
        self._audit.log(
            action, f"user:{result.user_id or 'unknown'}", 
            "success" if result.authenticated else "denied",
            user_id=result.user_id,
            details={"method": result.method, "error": result.error},
        )


# ═══════════════════════════════════════════════════════════════
# JWT TOKEN BUILDER (for testing + API key issuance)
# ═══════════════════════════════════════════════════════════════

def build_hs256_token(payload: Dict[str, Any], secret: str) -> str:
    """Build an HS256 JWT token (stdlib only). For testing + dev."""
    header = {"alg": "HS256", "typ": "JWT"}

    def b64url(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

    header_b64 = b64url(json.dumps(header, separators=(",", ":")).encode())
    payload_b64 = b64url(json.dumps(payload, separators=(",", ":")).encode())

    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    signature = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
    sig_b64 = b64url(signature)

    return f"{header_b64}.{payload_b64}.{sig_b64}"
