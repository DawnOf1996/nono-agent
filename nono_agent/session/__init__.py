"""会话存储 —— 内存 MessageStore + append-only 事件流（落盘只有事件）。

引擎只向 ``TranscriptWriter`` 追加 ``SessionEvent``；消息数组不落盘，
resume / fork / replay 都由 ``derive_messages()`` 从事件流推导。
"""

from nono_agent.session.db_transcript import SQLiteTranscriptWriter
from nono_agent.session.derive import (
    ReconstructionError,
    derive_messages,
    derived_to_messages,
    fold_request_header,
    message_to_derived,
    messages_to_derived,
    verify_reconstruction,
)
from nono_agent.session.events import (
    KNOWN_EVENT_TYPES,
    SESSION_FORMAT_VERSION,
    SessionEvent,
    SessionEventSink,
)
from nono_agent.session.factory import (
    DEFAULT_TRANSCRIPT_BACKEND,
    create_transcript_writer,
)
from nono_agent.session.fork import fork_transcript
from nono_agent.session.header import (
    SessionHeader,
    VersionMismatchError,
    check_format_version,
    make_header,
)
from nono_agent.session.redact import (
    redact_event,
    redact_events,
    redact_text,
    redact_value,
)
from nono_agent.session.replay import replay_sdk_messages, replay_transcript_text
from nono_agent.session.request_header import (
    canonical_header,
    header_equals,
    next_reason,
)
from nono_agent.session.store import MessageStore
from nono_agent.session.transcript import (
    DEFAULT_SESSION_DIR,
    SESSION_FILE_MODE,
    LocalFileTranscriptWriter,
    TranscriptWriter,
    list_session_files,
)

__all__ = [
    "DEFAULT_SESSION_DIR",
    "DEFAULT_TRANSCRIPT_BACKEND",
    "KNOWN_EVENT_TYPES",
    "LocalFileTranscriptWriter",
    "MessageStore",
    "ReconstructionError",
    "SESSION_FILE_MODE",
    "SESSION_FORMAT_VERSION",
    "SQLiteTranscriptWriter",
    "SessionEvent",
    "SessionEventSink",
    "SessionHeader",
    "TranscriptWriter",
    "VersionMismatchError",
    "canonical_header",
    "check_format_version",
    "create_transcript_writer",
    "derive_messages",
    "derived_to_messages",
    "fold_request_header",
    "fork_transcript",
    "header_equals",
    "list_session_files",
    "make_header",
    "message_to_derived",
    "messages_to_derived",
    "next_reason",
    "redact_event",
    "redact_events",
    "redact_text",
    "redact_value",
    "replay_sdk_messages",
    "replay_transcript_text",
    "verify_reconstruction",
]
