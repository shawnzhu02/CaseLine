"""CaseLine telecom gateway (spec §3 "Guava-only telecom adapter contract").

`CallGateway` is a CaseLine interface, not an SDK API. `GuavaCallGateway` implements it with guava-sdk 0.45.0;
`MockCallGateway` is the fake of the same interface for tests and the mock demo. Nothing here chooses numbers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class FieldSpec:
    key: str
    description: str
    field_type: str = "text"
    question: str | None = None
    required: bool = True
    choices: list[str] | None = None


@dataclass(frozen=True)
class SaySpec:
    text: str


ChecklistItem = FieldSpec | SaySpec | str


class CallGateway(Protocol):
    @property
    def call_id(self) -> str: ...
    @property
    def caller_id_number(self) -> str | None: ...
    def start_task(self, task_id: str, objective: str, checklist: list[ChecklistItem],
                   completion_criteria: str = "") -> None: ...
    def get_field(self, key: str) -> Any: ...
    def set_variable(self, key: str, value: Any) -> None: ...
    def get_variable(self, key: str, default: Any = None) -> Any: ...
    def transfer(self, authorized_destination: str, instructions: str) -> None: ...
    def end_call(self, final_instructions: str) -> None: ...
    def send_instruction(self, instruction: str) -> None: ...


class GuavaCallGateway:
    """Adapter over a live `guava.Call`. Imported only by the voice app."""

    def __init__(self, call) -> None:
        self._call = call

    @property
    def call_id(self) -> str:
        return self._call.id

    @property
    def caller_id_number(self) -> str | None:
        # Caller ID may be absent (anonymous) and is never treated as verified.
        return getattr(self._call.call_info, "from_number", None)

    def send_instruction(self, instruction: str) -> None:
        self._call.send_instruction(instruction)

    def start_task(self, task_id: str, objective: str, checklist: list[ChecklistItem],
                   completion_criteria: str = "") -> None:
        import guava

        items: list = []
        for item in checklist:
            if isinstance(item, FieldSpec):
                kwargs: dict[str, Any] = {"key": item.key, "description": item.description,
                                          "field_type": item.field_type, "required": item.required}
                # guava.Field rejects None for these; omit them when unset.
                if item.question is not None:
                    kwargs["question"] = item.question
                if item.choices is not None:
                    kwargs["choices"] = item.choices
                items.append(guava.Field(**kwargs))
            elif isinstance(item, SaySpec):
                items.append(guava.Say(item.text))
            else:
                items.append(item)
        self._call.set_task(task_id, objective=objective, checklist=items, completion_criteria=completion_criteria)

    def get_field(self, key: str) -> Any:
        return self._call.get_field(key)

    def set_variable(self, key: str, value: Any) -> None:
        self._call.set_variable(key, value)

    def get_variable(self, key: str, default: Any = None) -> Any:
        return self._call.get_variable(key, default)

    def transfer(self, authorized_destination: str, instructions: str) -> None:
        # guava-sdk 0.45.0: Call.transfer(destination, instructions) sends a soft TransferCommand. It returns
        # nothing and the SDK exposes no answered/failed callback, so success is never inferred from this call.
        self._call.transfer(authorized_destination, instructions)

    def end_call(self, final_instructions: str) -> None:
        self._call.hangup(final_instructions)


@dataclass
class MockCallGateway:
    """Records everything; never dials. Fields are pre-seeded to simulate what the caller said."""

    call_id: str = "mock-call"
    caller_id_number: str | None = None
    fields: dict[str, Any] = field(default_factory=dict)
    variables: dict[str, Any] = field(default_factory=dict)
    tasks: list[tuple[str, list[ChecklistItem]]] = field(default_factory=list)
    transfers: list[tuple[str, str]] = field(default_factory=list)
    ended_with: str | None = None
    instructions: list[str] = field(default_factory=list)

    def start_task(self, task_id: str, objective: str, checklist: list[ChecklistItem],
                   completion_criteria: str = "") -> None:
        self.tasks.append((task_id, checklist))

    def send_instruction(self, instruction: str) -> None:
        self.instructions.append(instruction)

    def get_field(self, key: str) -> Any:
        return self.fields.get(key)

    def set_variable(self, key: str, value: Any) -> None:
        self.variables[key] = value

    def get_variable(self, key: str, default: Any = None) -> Any:
        return self.variables.get(key, default)

    def transfer(self, authorized_destination: str, instructions: str) -> None:
        self.transfers.append((authorized_destination, instructions))

    def end_call(self, final_instructions: str) -> None:
        self.ended_with = final_instructions

    @property
    def current_task(self) -> str | None:
        return self.tasks[-1][0] if self.tasks else None
