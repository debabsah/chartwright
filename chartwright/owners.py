"""`dashboard.owners`: the Superset accounts that own a dashboard, named in the spec.

Superset facts this module is built around (docs/CONTRACTS.md, "Dashboard owners"):

- The import bundle has no owners field (ImportV1DashboardSchema at 4.1.4, 5.0.0 and
  6.1.0), and the importer makes the importing account an owner on every import
  (commands/dashboard/importers/v1/utils.py). Owners are therefore set after the
  import, by PUT /api/v1/dashboard/<id> {"owners": [user ids]}: DashboardPutSchema
  takes user ids only. Roles are a different field (`roles`, dashboard access under
  DASHBOARD_RBAC), not owners.
- The REST API never returns a username for another account: the dashboard's
  `owners` exclude it (UserSchema(exclude=["username"])), and
  /api/v1/dashboard/related/owners returns id, full name and email, searching
  username or full name by substring. Only the security API
  (/api/v1/security/users/, on when FAB_ADD_SECURITY_API is set: the 6.1.0 default,
  off by default before) returns usernames. So a username is confirmed exactly
  where that API answers; an email address is confirmed on every release.
- The account that applies stays an owner. Superset re-adds it on every import, and
  a non-admin account that isn't an owner can't import over the dashboard again
  ("A dashboard already exists and user doesn't have permissions to overwrite it"),
  nor drop itself from the owners by PUT (commands/utils.py populate_owner_list).
"""

from __future__ import annotations

import base64
import difflib
import json
from dataclasses import dataclass, field

from .client import SupersetAPIError

RELATED = "/api/v1/dashboard/related/owners"
USERS = "/api/v1/security/users/"
PAGE = 100          # FAB's largest page
PAGE_CAP = 200      # 20,000 accounts: a runaway guard, not an expected ceiling


@dataclass
class Account:
    id: int
    name: str                   # "First Last", as Superset shows it
    email: str | None = None
    username: str | None = None  # only where the security API answers

    def describe(self) -> str:
        who = self.username or self.email or f"id {self.id}"
        return f"{who} ({self.name})" if self.name and self.name != who else who


@dataclass
class OwnerError:
    code: str                   # owner_not_found | owner_ambiguous | owner_account_unknown
    ref: str
    detail: str
    candidates: list[str] = field(default_factory=list)


class Directory:
    """The instance's accounts, read through whichever API the instance offers.
    One per run: it caches whether the security API answers."""

    def __init__(self, client):
        self.client = client
        self._users_api: bool | None = None

    # -- the security API (usernames) ----------------------------------------

    def _users(self, filters: list[dict]) -> list[Account] | None:
        """Accounts matching FAB filters, or None when the security API doesn't
        answer for this account (absent, or not permitted)."""
        if self._users_api is False:
            return None
        try:
            out = self.client.get(USERS, q={
                "filters": filters, "page_size": PAGE,
                "columns": ["id", "username", "email", "first_name", "last_name"]})
        except SupersetAPIError as e:
            if e.status in (401, 403, 404, 405):
                self._users_api = False
                return None
            raise
        self._users_api = True
        return [Account(int(u["id"]), f"{u.get('first_name') or ''} {u.get('last_name') or ''}".strip(),
                        u.get("email"), u.get("username")) for u in out.get("result") or []]

    def usernames_readable(self) -> bool:
        if self._users_api is None:
            self._users([{"col": "id", "opr": "eq", "value": 0}])
        return bool(self._users_api)

    def by_username(self, username: str) -> list[Account] | None:
        found = self._users([{"col": "username", "opr": "eq", "value": username}])
        return None if found is None else [a for a in found if a.username == username]

    # -- the related-owners list (every release: id, full name, email) --------

    def search(self, text: str | None = None) -> list[Account]:
        """Accounts whose username or full name contains `text` (Superset's
        FilterRelatedOwners), or every listed account when text is None."""
        accounts: list[Account] = []
        for page in range(PAGE_CAP):
            q: dict = {"page": page, "page_size": PAGE}
            if text:
                q["filter"] = text
            out = self.client.get(RELATED, q=q)
            rows = out.get("result") or []
            accounts += [self._from_related(r) for r in rows]
            if len(rows) < PAGE or len(accounts) >= (out.get("count") or 0):
                break
        return accounts

    @staticmethod
    def _from_related(row: dict) -> Account:
        extra = row.get("extra") or {}
        return Account(int(row["value"]), row.get("text") or "", extra.get("email"))

    def by_email(self, email: str) -> list[Account]:
        """Accounts with this email address (case-insensitive). The related list
        doesn't search emails, so the search goes by the address's local part (which
        usually appears in the username or the name) and falls back to every account."""
        want = email.casefold()
        local = email.split("@", 1)[0]
        for pool in (lambda: self.search(local), lambda: self.search()):
            hits = [a for a in pool() if (a.email or "").casefold() == want]
            if hits:
                return hits
        return []

    def by_ids(self, ids: list[int]) -> dict[int, Account]:
        """id -> account, with usernames where the security API answers."""
        out: dict[int, Account] = {}
        if not ids:
            return out
        rows = self.client.get(RELATED, q={"include_ids": list(ids), "page": 0,
                                           "page_size": 1}).get("result") or []
        for r in rows:
            a = self._from_related(r)
            if a.id in ids:
                out[a.id] = a
        if self.usernames_readable():
            for i in ids:
                found = self._users([{"col": "id", "opr": "eq", "value": i}]) or []
                for a in found:
                    if a.id == i:
                        out[i] = Account(i, a.name, a.email or (out.get(i) and out[i].email),
                                         a.username)
        return out


def signed_in_account_id(client) -> int | None:
    """The id of the account the client signed in as. Superset's sign-in token
    carries it as its subject on every supported release (FAB's security API signs
    the user id in); /api/v1/me/ is the fallback, since it accepts a token only on
    6.1.0 (4.1.4 and 5.0.0 answer 401)."""
    auth = (getattr(client, "session", None) and client.session.headers.get("Authorization")) or ""
    token = auth.split(" ", 1)[1] if auth.startswith("Bearer ") else ""
    try:
        body = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        return int(claims["sub"])
    except (IndexError, ValueError, KeyError, TypeError):
        pass
    try:
        me = client.get("/api/v1/me/").get("result") or {}
    except SupersetAPIError:
        return None
    return me["id"] if isinstance(me.get("id"), int) else None


def _value_for(account: Account) -> str:
    """What a spec writes for this account: its username where readable, else its email."""
    return account.username or account.email or ""


def _values(directory: Directory, accounts: list[Account]) -> list[str]:
    """The spec value for each account (usernames filled in where readable)."""
    if accounts and directory.usernames_readable():
        named = directory.by_ids([a.id for a in accounts if not a.username])
        accounts = [a if a.username else named.get(a.id, a) for a in accounts]
    return [v for v in (_value_for(a) for a in accounts) if v]


def _suggestions(directory: Directory, name: str) -> list[str]:
    """Close spec values for a name that matched no account: the accounts whose
    username or full name contains it, or else its first three or two letters,
    best first. At most three searches, so a typo costs little."""
    rows: list[Account] = []
    for probe in dict.fromkeys(p for p in (name, name[:3], name[:2]) if len(p) >= 2):
        rows = directory.search(probe)
        if rows:
            break
    values = sorted(set(_values(directory, rows[:20])))
    return difflib.get_close_matches(name, values, n=5, cutoff=0) or values[:5]


def resolve_owners(names: list[str], client) -> tuple[list[int] | None, list[OwnerError]]:
    """Spec owners -> the user ids apply PUTs: each named account, plus the account
    that signs in (kept an owner, see the module docstring). Every name is checked
    before anything is written; an error names the replacement values to try."""
    directory = Directory(client)
    ids: list[int] = []
    errors: list[OwnerError] = []
    for name in names:
        matches: dict[int, Account] = {}
        by_name = directory.by_username(name)
        for a in by_name or []:
            matches[a.id] = a
        if "@" in name:
            for a in directory.by_email(name):
                matches.setdefault(a.id, a)
        if len(matches) == 1:
            ids.append(next(iter(matches)))
            continue
        if matches:
            errors.append(OwnerError(
                "owner_ambiguous", name,
                f"{name!r} names more than one Superset account "
                f"({', '.join(a.describe() for a in matches.values())}); name one by its "
                f"username or email", _values(directory, list(matches.values()))))
            continue
        if by_name is None and "@" not in name:
            errors.append(OwnerError(
                "owner_not_found", name,
                f"this instance's API returns no usernames (Superset 4.1.4 and 5.0.0 "
                f"unless FAB_ADD_SECURITY_API is on), so {name!r} can't be confirmed as "
                f"one; name the owner by the email address of their Superset account",
                _suggestions(directory, name)))
            continue
        errors.append(OwnerError(
            "owner_not_found", name,
            f"no Superset account has the {'username or email' if '@' in name else 'username'} "
            f"{name!r}", _suggestions(directory, name)))
    if errors:
        return None, errors
    account = signed_in_account_id(client)
    if account is None:
        return None, [OwnerError(
            "owner_account_unknown", "dashboard.owners",
            "can't tell which Superset account is signed in, and that account must stay "
            "an owner for the next apply to import over the dashboard; omit owners, or "
            "sign in with a Superset username and password")]
    return sorted(set(ids) | {account}), []


def live_owner_ids(client, dashboard_id: int) -> list[int]:
    detail = client.get(f"/api/v1/dashboard/{dashboard_id}")["result"]
    return sorted(int(o["id"]) for o in detail.get("owners") or [] if "id" in o)


def owner_names(client, ids: list[int]) -> tuple[list[str] | None, str | None]:
    """The spec values for these owner ids (usernames where readable, else emails),
    sorted, or (None, why) when any of them can't be named: a partial list would
    drop an owner on the next apply."""
    if not ids:
        return [], None
    accounts = Directory(client).by_ids(ids)
    missing = [i for i in ids if not _value_for(accounts.get(i) or Account(i, ""))]
    if missing:
        return None, (f"owners not read back: no username or email readable for "
                      f"account id(s) {missing}; omitted, so apply leaves the owners alone")
    return sorted(_value_for(accounts[i]) for i in ids), None


def set_owners(client, dashboard_id: int, ids: list[int]) -> str | None:
    """PUT the dashboard's owners; an error message, or None."""
    r = client.put_json(f"/api/v1/dashboard/{dashboard_id}", {"owners": list(ids)})
    if r.status_code != 200:
        return f"owners PUT failed: HTTP {r.status_code} {r.text[:300]}"
    return None
