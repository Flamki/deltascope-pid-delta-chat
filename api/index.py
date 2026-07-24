"""Vercel Python Function entry point."""

from app import Handler, restore_sessions


restore_sessions()


class handler(Handler):
    pass
