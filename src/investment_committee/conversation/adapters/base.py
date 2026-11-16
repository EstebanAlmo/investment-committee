"""ConversationAdapter port.

See docs/specs/spec-006-conversational-interface.md S21. `ConversationService`
(conversation/service.py) is scoped to a later phase and does not exist yet
- the constructor parameter below is typed against it anyway, since that is
this port's real, spec-defined shape. This is safe today only because every
module in this codebase starts with `from __future__ import annotations`
(PEP 563): annotations are never evaluated at runtime, so a forward
reference to a not-yet-existing module does not raise ImportError as long
as the import itself stays inside `if TYPE_CHECKING`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, ClassVar

from ..models import ChannelType, ConversationIdentity, ConversationResponse

if TYPE_CHECKING:
    from ..service import ConversationService


class ConversationAdapter(ABC):
    """One channel's boundary. Owns no business logic - every inbound event
    becomes exactly one ConversationService.handle_message() call, and every
    outbound reply is exactly what that call returned, unmodified.
    """

    channel: ClassVar[ChannelType]

    def __init__(self, conversation_service: ConversationService) -> None:
        self._conversation_service = conversation_service

    @abstractmethod
    def send(self, response: ConversationResponse, conversation_identity: ConversationIdentity) -> None:
        """Deliver `response` back to the channel, in whatever native shape
        the channel needs (text message, voice note, etc.).
        """
        raise NotImplementedError
