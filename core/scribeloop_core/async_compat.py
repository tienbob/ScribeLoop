"""Deadline context manager for Ubuntu 22.04's Python 3.10 and newer."""
import sys

if sys.version_info >= (3, 11):
    from asyncio import timeout
else:
    from async_timeout import timeout

__all__ = ['timeout']
