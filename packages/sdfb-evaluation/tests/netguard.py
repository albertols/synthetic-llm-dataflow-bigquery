#  Copyright 2026 The synthetic-llm-dataflow-bigquery Authors
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      https://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
"""No test of this package reaches the network.

A `NetworkGuard`, while installed, replaces the three ways Python code
leaves the machine or goes looking for credentials:

    socket.socket.connect / connect_ex   a non-loopback address
    socket.getaddrinfo                   a host name other than localhost
    google.auth.default                  Application Default Credentials
                                         (Beam and the Google clients ask
                                         for them before their first call)

Each attempt is recorded and fails the test at once (`pytest.fail`, which
`except Exception` does not catch); one that the code under test swallows
anyway fails the test when it ends (`verify`). Loopback addresses and
unix sockets stay open: the DirectRunner and its local services use them.

`tests/conftest.py` installs one guard for the whole session — so
module- and session-scoped fixtures are guarded too — and lifts it only
around a test marked `gcp`.

Design: docs/designs/2026-07-07-evaluation-framework-design.md
"""

from __future__ import annotations

import ipaddress
import socket
from typing import Any

import google.auth
import google.auth._default
import pytest

__all__ = ["NetworkGuard"]

_LOCAL_NAMES = frozenset({"", "localhost"})


def _is_local_host(host: Any) -> bool:
  if host is None:
    return True
  if isinstance(host, bytes):
    host = host.decode("ascii", "replace")
  text = str(host)
  if text.lower() in _LOCAL_NAMES:
    return True
  try:
    return ipaddress.ip_address(text.split("%", 1)[0]).is_loopback
  except ValueError:
    return False  # a host name: resolving it leaves the machine


def _is_local_address(address: Any) -> bool:
  """A unix socket path, or an (host, port, …) on this machine."""
  if isinstance(address, (str, bytes)):
    return True
  return isinstance(address, tuple) and bool(address) and _is_local_host(
      address[0])


class NetworkGuard:
  """Refuses the network while installed (module docstring). `attempts`
  holds one text per attempt since the last `verify`."""

  def __init__(self) -> None:
    self.attempts: list[str] = []
    self._patch: pytest.MonkeyPatch | None = None

  def _refuse(self, what: str) -> None:
    self.attempts.append(what)
    pytest.fail(f"the test tried to reach the network: {what} (a test that "
                "needs GCP is marked `gcp`; anything else uses a fake)")

  def install(self) -> None:
    """Start refusing; a guard already installed stays as it is."""
    if self._patch is not None:
      return
    connect, connect_ex = socket.socket.connect, socket.socket.connect_ex
    getaddrinfo = socket.getaddrinfo
    refuse = self._refuse

    def guarded_connect(sock: socket.socket, address: Any) -> Any:
      if not _is_local_address(address):
        refuse(f"connect({address!r})")
      return connect(sock, address)

    def guarded_connect_ex(sock: socket.socket, address: Any) -> Any:
      if not _is_local_address(address):
        refuse(f"connect_ex({address!r})")
      return connect_ex(sock, address)

    def guarded_getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
      if not _is_local_host(host):
        refuse(f"getaddrinfo({host!r})")
      return getaddrinfo(host, *args, **kwargs)

    def no_credentials(*args: Any, **kwargs: Any) -> Any:
      del args, kwargs
      refuse("google.auth.default()")

    patch = pytest.MonkeyPatch()
    patch.setattr(socket.socket, "connect", guarded_connect)
    patch.setattr(socket.socket, "connect_ex", guarded_connect_ex)
    patch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)
    patch.setattr(google.auth, "default", no_credentials)
    patch.setattr(google.auth._default, "default", no_credentials)  # pylint: disable=protected-access  # the module the clients import the function from
    self._patch = patch

  def remove(self) -> None:
    """Stop refusing (the real functions are back)."""
    if self._patch is not None:
      self._patch.undo()
      self._patch = None

  def verify(self) -> None:
    """Fail the test if an attempt is still recorded, and forget them.

    Raises:
      pytest.fail.Exception: there was one (it names each).
    """
    attempts, self.attempts[:] = list(self.attempts), []
    if attempts:
      listed = "; ".join(attempts)
      pytest.fail(f"the test tried to reach the network: {listed}")
