"""Domain exceptions shared by every layer.

Lower layers raise these; the API layer maps each category to one HTTP status,
and the agent's tool runner turns them into `{"error": ...}` for the model.
Messages are written for the end user: they say what went wrong and, where it
helps, what to do about it.
"""


class KnowledgeAssistantError(Exception):
    """Base class for every error this application raises on purpose."""


# --- Uploads that can't be ingested ---


class InvalidDocumentError(KnowledgeAssistantError):
    """An uploaded file was rejected before or during ingestion."""


class UnsupportedFileError(InvalidDocumentError):
    """The file isn't a PDF, DOCX or TXT, or its content doesn't match its extension."""


class FileTooLargeError(InvalidDocumentError):
    """The file is bigger than the configured MAX_UPLOAD_MB."""

    def __init__(self, size_bytes: int, limit_bytes: int) -> None:
        super().__init__(
            f"File is {size_bytes / 1_048_576:.1f} MB; "
            f"the limit is {limit_bytes / 1_048_576:.0f} MB."
        )
        self.size_bytes = size_bytes
        self.limit_bytes = limit_bytes


class EmptyDocumentError(InvalidDocumentError):
    """The file is empty, or has no extractable text (e.g. a scan with OCR off)."""


class CorruptDocumentError(InvalidDocumentError):
    """The file has a supported type but can't be parsed (truncated, damaged,
    encrypted)."""


# --- Document catalogue ---


class DuplicateDocumentError(KnowledgeAssistantError):
    """A document with the same content (SHA-256) has already been uploaded."""

    def __init__(self, filename: str) -> None:
        super().__init__(f"This file was already uploaded as {filename!r}.")
        self.filename = filename


class DocumentNotFoundError(KnowledgeAssistantError):
    """No document exists with the requested id."""

    def __init__(self, document_id: int) -> None:
        super().__init__(f"Document {document_id} not found.")
        self.document_id = document_id


# --- Language model / agent ---


class AssistantError(KnowledgeAssistantError):
    """The LLM-backed answer could not be produced."""


class AssistantNotConfiguredError(AssistantError):
    """OPENAI_API_KEY is not set, so questions can't be answered; uploads and
    listing still work."""


class AssistantUnavailableError(AssistantError):
    """The OpenAI call failed: network error, timeout, rate limit or API error."""


class AgentStepLimitError(AssistantError):
    """The agent used all AGENT_MAX_ROUNDS tool rounds without giving an answer."""

    def __init__(self, max_rounds: int) -> None:
        super().__init__(
            f"The assistant didn't finish within {max_rounds} steps. "
            "Try a simpler or more specific question."
        )
        self.max_rounds = max_rounds
