from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.modules.chatbot.services.ai_provider import AIProviderTool, AIProviderToolCall


class _StrictArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _EmptyArguments(_StrictArguments):
    pass


class QueryMyTicketsArguments(_StrictArguments):
    operation: Literal["LIST", "COUNT", "GET"]
    status: (
        Literal[
            "ALL",
            "OPEN",
            "NUEVO",
            "ASIGNADO",
            "EN_PROCESO",
            "PENDIENTE_CLIENTE",
            "RESUELTO",
            "CERRADO",
            "CANCELADO",
        ]
        | None
    ) = None
    tracking_code: str | None = Field(default=None, max_length=30)
    position: int | None = Field(default=None, ge=1, le=5)
    fields: list[Literal["CODE", "SUBJECT", "STATUS", "DESCRIPTION"]] = Field(
        default_factory=list, max_length=4
    )
    page: int = Field(default=1, ge=1, le=10_000)


class UpdateTicketDraftArguments(_StrictArguments):
    category_name: str | None = Field(default=None, max_length=100)
    subject: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=10_000)
    priority: Literal["BAJA", "MEDIA", "ALTA", "URGENTE"] | None = None


class SearchPublicFAQArguments(_StrictArguments):
    query: str = Field(min_length=1, max_length=500)


class AskClarificationArguments(_StrictArguments):
    field: (
        Literal[
            "CATEGORY",
            "SUBJECT",
            "DESCRIPTION",
            "PRIORITY",
            "TICKET_REFERENCE",
            "TOPIC",
        ]
        | None
    ) = None


class StartTicketDraftArguments(UpdateTicketDraftArguments):
    pass


_ARGUMENT_MODELS: dict[str, type[BaseModel]] = {
    "ask_clarification": AskClarificationArguments,
    "query_my_tickets": QueryMyTicketsArguments,
    "respond": _EmptyArguments,
    "search_public_faq": SearchPublicFAQArguments,
    "show_options": _EmptyArguments,
    "start_ticket_draft": StartTicketDraftArguments,
    "update_ticket_draft": UpdateTicketDraftArguments,
}


@dataclass(frozen=True)
class _ToolDefinition:
    name: str
    description: str
    arguments: type[BaseModel]


_DEFINITIONS = (
    _ToolDefinition(
        "ask_clarification",
        "Ask one focused question when the user intent or a required field is unclear.",
        AskClarificationArguments,
    ),
    _ToolDefinition(
        "query_my_tickets",
        "List, count, or inspect only the authenticated user's own tickets. "
        "The backend enforces identity and ownership.",
        QueryMyTicketsArguments,
    ),
    _ToolDefinition(
        "respond",
        "Answer a greeting or a short conversational turn without making "
        "banking claims.",
        _EmptyArguments,
    ),
    _ToolDefinition(
        "search_public_faq",
        "Retrieve public banking information from active FAQs with verified "
        "official sources.",
        SearchPublicFAQArguments,
    ),
    _ToolDefinition(
        "show_options",
        "Show the options generated and validated by the backend.",
        _EmptyArguments,
    ),
    _ToolDefinition(
        "start_ticket_draft",
        "Start or resume a request draft. This never confirms or creates a ticket.",
        StartTicketDraftArguments,
    ),
    _ToolDefinition(
        "update_ticket_draft",
        "Extract fields explicitly stated by the user for the current draft. "
        "Do not infer priority from wording.",
        UpdateTicketDraftArguments,
    ),
)


def available_chatbot_tools() -> tuple[AIProviderTool, ...]:
    return tuple(
        AIProviderTool(
            name=item.name,
            description=item.description,
            parameters=_strict_argument_schema(item.arguments),
        )
        for item in _DEFINITIONS
    )


def validate_tool_call(
    call: AIProviderToolCall | None,
    *,
    allowed_tools: tuple[AIProviderTool, ...],
) -> AIProviderToolCall | None:
    if call is None:
        return None
    if call.name not in {item.name for item in allowed_tools}:
        return None
    model = _ARGUMENT_MODELS.get(call.name)
    if model is None:
        return None
    try:
        validated = model.model_validate(call.arguments)
    except ValidationError:
        return None
    return AIProviderToolCall(
        name=call.name,
        arguments=validated.model_dump(mode="json"),
    )


def _strict_argument_schema(model: type[BaseModel]) -> dict[str, object]:
    schema = model.model_json_schema()
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        properties = {}
    definitions = schema.get("$defs")
    if isinstance(definitions, dict):
        properties = {
            name: _inline_schema(value, definitions)
            for name, value in properties.items()
        }
    else:
        properties = dict(properties)
    required = list(properties)
    # Provider strict mode requires every declared property to appear.
    for _, value in tuple(properties.items()):
        if isinstance(value, dict) and "default" in value:
            value.pop("default", None)
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _inline_schema(value: object, definitions: dict[str, object]) -> object:
    if isinstance(value, list):
        return [_inline_schema(item, definitions) for item in value]
    if not isinstance(value, dict):
        return value
    reference = value.get("$ref")
    if isinstance(reference, str) and reference.startswith("#/$defs/"):
        definition = definitions.get(reference.removeprefix("#/$defs/"))
        if isinstance(definition, dict):
            return _inline_schema(definition, definitions)
    return {
        key: _inline_schema(item, definitions)
        for key, item in value.items()
        if key != "$defs"
    }


__all__ = ["available_chatbot_tools", "validate_tool_call"]
