"""Server-side admin setup. Super admins can only be created here, never from the web.

    python -m elaw_core.cli create-admin --email you@example.com --name "Your Name"
    python -m elaw_core.cli create-admin --email ... --role ADMIN
    python -m elaw_core.cli promote --email someone@example.com --role ADMIN

The password is asked interactively (or read from ELAW_ADMIN_PASSWORD) and never echoed.
"""

import argparse
import getpass
import os
import sys

from sqlalchemy import select

from elaw_core import security
from elaw_core.db import session_scope
from elaw_core.models import Role, Status, User


def _password(email):
    pw = os.environ.get("ELAW_ADMIN_PASSWORD")
    if not pw:
        pw = getpass.getpass("Admin password: ")
        if getpass.getpass("Repeat password: ") != pw:
            sys.exit("Passwords don't match.")
    problem = security.validate_password(pw, email)
    if problem:
        sys.exit(problem)
    return pw


def create_admin(email, name, role):
    email = security.normalize_email(email)
    if security.validate_email(email):
        sys.exit("Invalid email.")
    with session_scope() as db:
        user = db.scalar(select(User).where(User.email == email))
        pw = _password(email)
        if user is None:
            user = User(name=name or email.split("@")[0], email=email)
            db.add(user)
        user.password_hash = security.hash_password(pw)
        user.role = Role(role)
        user.status = Status.ACTIVE
        user.email_verified = True
    print(f"{role} account ready for {email}. Log in at /login, then open /admin.")


def promote(email, role):
    email = security.normalize_email(email)
    with session_scope() as db:
        user = db.scalar(select(User).where(User.email == email))
        if user is None:
            sys.exit("No account with that email.")
        user.role = Role(role)
    print(f"{email} is now {role}.")


def main(argv=None):
    p = argparse.ArgumentParser(prog="elaw_core.cli")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create-admin")
    c.add_argument("--email", required=True)
    c.add_argument("--name", default="")
    c.add_argument("--role", default="SUPER_ADMIN", choices=[r.value for r in Role if r != Role.USER])
    pr = sub.add_parser("promote")
    pr.add_argument("--email", required=True)
    pr.add_argument("--role", required=True, choices=[r.value for r in Role])
    args = p.parse_args(argv)
    if args.cmd == "create-admin":
        create_admin(args.email, args.name, args.role)
    else:
        promote(args.email, args.role)


if __name__ == "__main__":
    main()
