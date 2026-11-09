"""The org and user a request acts for.

Every org-scoped endpoint takes a `Tenant`, and every query it makes filters on `tenant.org.id`
and `tenant.user.id`. Both come from the URL and the session cookie, checked against each other,
never from the request body or headers.
"""

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, status

from app.auth.dependencies import CurrentUser
from app.db.models import Organization, User
from app.tenancy.orgs import OrgFromPath


@dataclass(frozen=True)
class TenantContext:
    org: Organization
    user: User


def get_tenant(org: OrgFromPath, user: CurrentUser) -> TenantContext:
    """403 when a signed-in user calls another org's endpoints, e.g. by editing the URL."""
    if user.org_id != org.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You don't have access to this organization.")
    return TenantContext(org=org, user=user)


Tenant = Annotated[TenantContext, Depends(get_tenant)]
