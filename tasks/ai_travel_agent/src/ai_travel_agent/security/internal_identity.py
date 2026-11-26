"""Headers the agent uses to call this API as the signed-in user.

`X-Internal-Token` carries the per-process secret from the container and
`X-Acting-User-Id` the user the call is made for. Kept in their own module so
the agent's HTTP client can use them without importing the auth dependencies.
"""

INTERNAL_TOKEN_HEADER = "X-Internal-Token"
ACTING_USER_HEADER = "X-Acting-User-Id"
