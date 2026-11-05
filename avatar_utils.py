"""Shared avatar selection for HTML views and PvP payloads."""

from flask import url_for


def avatar_url(user=None, *, custom_avatar=None, google_picture=None):
    """Return the preferred public avatar URL without exposing storage paths."""
    if user is not None:
        custom_avatar = getattr(user, "custom_avatar", None)
        google_picture = getattr(user, "picture", None)
    if custom_avatar:
        return url_for("serve_avatar", filename=custom_avatar)
    return google_picture or ""
