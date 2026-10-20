"""Abstract transfer interface for CRIS file delivery.

Provides a base class so the transfer protocol (SFTP, REST, MQ …) can be
swapped without touching the exporter logic.
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class CrisTransferClient(ABC):
    """Abstract CRIS file transfer client."""

    @abstractmethod
    def upload(self, local_path: str, remote_name: str) -> None:
        """Transfer *local_path* to the CRIS destination.

        Args:
            local_path:  Absolute path of the file to send.
            remote_name: Desired filename on the remote server.

        Raises:
            RuntimeError: If the transfer fails for any reason.
        """
