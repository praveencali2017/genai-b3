"""Chatbot tools: OpenAI function schemas plus handlers that run the employee
CRUD operations with the same validation as the REST API."""

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from employees import (
    DuplicateEmailError,
    Employee,
    EmployeeCreate,
    EmployeeNotFoundError,
    EmployeeUpdate,
    create_employee,
    delete_employee,
    get_employee,
    list_employees,
    update_employee,
)

logger = logging.getLogger(__name__)

_EMPLOYEE_PROPERTIES: dict[str, Any] = {
    "name": {
        "type": "string",
        "description": "Full name, 1-100 characters, exactly as the user gave it.",
    },
    "email": {
        "type": "string",
        "description": "Valid email address, unique across employees "
        "(case-insensitive). Never invent one.",
    },
    "department": {
        "type": "string",
        "description": "Department name, 1-100 characters, e.g. Engineering.",
    },
    "position": {
        "type": "string",
        "description": "Job title, 1-100 characters, e.g. Backend Developer.",
    },
    "salary": {
        "type": "number",
        "description": "Annual salary as a plain non-negative number with no "
        "currency symbol or separators: '95k' -> 95000. Never convert currencies.",
    },
    "hire_date": {
        "type": "string",
        "description": "Hire date as YYYY-MM-DD. Resolve relative dates such as "
        "'last Monday' against today's date from the instructions. Use null when "
        "the user didn't give one; never guess it.",
    },
}
_EMPLOYEE_ID_PROPERTY: dict[str, Any] = {
    "employee_id": {
        "type": "integer",
        "description": "Numeric id taken from a list_employees or get_employee "
        "result. Never guess an id from a name or position in a list.",
    }
}


def object_schema(
    properties: dict[str, Any], required: list[str] | None = None
) -> dict[str, Any]:
    """Build a strict-mode parameter schema.

    Strict mode makes the model send every property and nothing else, so the
    optional ones become nullable, where null means "unknown" or "leave
    unchanged". Without it the model tends to fill optional fields with blanks
    or invented values (e.g. today's date as a hire date).
    """
    required = required or []
    return {
        "type": "object",
        "properties": {
            name: spec if name in required else {**spec, "type": [spec["type"], "null"]}
            for name, spec in properties.items()
        },
        "required": list(properties),
        "additionalProperties": False,
    }


def function_tool(
    name: str, description: str, parameters: dict[str, Any]
) -> dict[str, Any]:
    return {
        "type": "function",
        "name": name,
        "description": description,
        "parameters": parameters,
        "strict": True,
    }


READ_TOOLS: list[dict[str, Any]] = [
    function_tool(
        "list_employees",
        "Read-only. List employees ordered by id, optionally filtered by a "
        "case-insensitive substring matched against name, email, department and "
        "position. Use it to resolve a name to an id, to check whether an employee "
        "exists, and to answer questions such as counts, totals, averages or 'who "
        "earns the most'. Pass search=null to get everyone. An empty list means no "
        "match.",
        object_schema(
            {
                "search": {
                    "type": "string",
                    "description": "Substring to match, e.g. a first name, last "
                    "name, email or department. Use the shortest distinctive part.",
                }
            }
        ),
    ),
    function_tool(
        "get_employee",
        "Read-only. Fetch one employee by id. Returns an error if the id doesn't "
        "exist.",
        object_schema(_EMPLOYEE_ID_PROPERTY, ["employee_id"]),
    ),
]

WRITE_TOOLS: list[dict[str, Any]] = [
    function_tool(
        "create_employee",
        "Create ONE employee. Call it only when the user has supplied name, email, "
        "department, position and salary; hire_date is optional (null if not "
        "given). Never fill missing fields with placeholders or guesses. These are "
        "the only fields an employee has. Fails if the email is already in use.",
        object_schema(
            _EMPLOYEE_PROPERTIES, ["name", "email", "department", "position", "salary"]
        ),
    ),
    function_tool(
        "update_employee",
        "Update ONE existing employee. Pass the id, the new value of each field "
        "that changes, and null for every other field; at least one field must be "
        "non-null. For relative changes ('raise by 10%'), first read the current "
        "value, compute the new absolute value, and pass that. Fails if the id "
        "doesn't exist or the new email belongs to another employee.",
        object_schema(
            {**_EMPLOYEE_ID_PROPERTY, **_EMPLOYEE_PROPERTIES}, ["employee_id"]
        ),
    ),
    function_tool(
        "delete_employee",
        "Permanently delete ONE employee; this cannot be undone. Before calling it, "
        "tell the user which employee (id, name, email) will be deleted and ask "
        "them to confirm. Call it only after their reply clearly confirms.",
        object_schema(
            {
                **_EMPLOYEE_ID_PROPERTY,
                "confirmed": {
                    "type": "boolean",
                    "description": "true only if the user explicitly confirmed "
                    "deleting this specific employee in the conversation.",
                },
            },
            ["employee_id", "confirmed"],
        ),
    ),
]

TOOLS: list[dict[str, Any]] = READ_TOOLS + WRITE_TOOLS


class ToolRefusedError(ValueError):
    """A tool call that's well-formed but not allowed as made; the message tells
    the model what to do instead."""


def _dump(employee: Employee) -> dict[str, Any]:
    return employee.model_dump(mode="json")


def _employee_id(args: dict[str, Any]) -> int:
    return int(args["employee_id"])


def _tool_list_employees(args: dict[str, Any]) -> list[dict[str, Any]]:
    search = (args.get("search") or "").strip() or None
    return [_dump(employee) for employee in list_employees(search)]


def _tool_get_employee(args: dict[str, Any]) -> dict[str, Any]:
    return _dump(get_employee(_employee_id(args)))


def _provided_fields(args: dict[str, Any]) -> dict[str, Any]:
    """Employee fields the model actually supplied. Strict mode sends null for
    unknown or unchanged fields; blank strings are treated the same way."""
    return {
        key: value
        for key, value in args.items()
        if key != "employee_id"
        and value is not None
        and not (isinstance(value, str) and not value.strip())
    }


def _tool_create_employee(args: dict[str, Any]) -> dict[str, Any]:
    return _dump(create_employee(EmployeeCreate(**_provided_fields(args))))


def _tool_update_employee(args: dict[str, Any]) -> dict[str, Any]:
    changes = EmployeeUpdate(**_provided_fields(args))
    if not changes.model_fields_set:
        raise ToolRefusedError("No fields to update. Ask the user what should change.")
    return _dump(update_employee(_employee_id(args), changes))


def _tool_delete_employee(args: dict[str, Any]) -> dict[str, Any]:
    employee = get_employee(_employee_id(args))
    if args.get("confirmed") is not True:
        raise ToolRefusedError(
            f"Not deleted. Ask the user to confirm deleting #{employee.id} "
            f"{employee.name} ({employee.email}) first."
        )
    delete_employee(employee.id)
    return {"deleted": _dump(employee)}


# Each handler validates the model's arguments with the same schemas the REST API uses.
TOOL_HANDLERS: dict[str, Callable[[dict[str, Any]], Any]] = {
    "list_employees": _tool_list_employees,
    "get_employee": _tool_get_employee,
    "create_employee": _tool_create_employee,
    "update_employee": _tool_update_employee,
    "delete_employee": _tool_delete_employee,
}


@dataclass(frozen=True)
class ToolResult:
    """Outcome of one tool call. `value` is JSON-serializable: the tool's return
    value, or {"error": ...} when the call failed."""

    value: Any
    is_error: bool

    def to_json(self) -> str:
        return json.dumps(self.value, default=str)


def _error(message: str, **extra: Any) -> ToolResult:
    return ToolResult({"error": message, **extra}, is_error=True)


def run_tool(name: str, raw_arguments: str) -> ToolResult:
    """Execute one tool call. Domain errors become {"error": ...} results so the
    model can explain them or retry with fixed input."""
    handler = TOOL_HANDLERS.get(name)
    if handler is None:
        return _error(f"Unknown tool {name!r}.")
    try:
        result = ToolResult(handler(json.loads(raw_arguments or "{}")), is_error=False)
    except (EmployeeNotFoundError, DuplicateEmailError, ToolRefusedError) as error:
        result = _error(str(error))
    except ValidationError as error:
        result = _error("Invalid arguments.", details=error.errors(include_url=False))
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
        result = _error(f"Malformed arguments: {error}")
    logger.info("tool %s(%s) -> %s", name, raw_arguments, result.value)
    return result
