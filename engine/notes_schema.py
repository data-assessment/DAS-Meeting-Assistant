"""Validated, backward-compatible derived notes; no audio or transcript fields."""
import re
import uuid
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

class Person(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=200)
    email: str = Field(default="", max_length=300)
    source: Literal["calendar", "self", "manual", "call", "contact"] = "manual"

class Question(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str = Field(default="", max_length=100)
    label: str = Field(min_length=1, max_length=1000)
    options: list[str] = Field(default_factory=list, max_length=5)
    answer: str = Field(default="", max_length=1000)
    field: Literal["context", "owner", "recipient"] = "context"

    @model_validator(mode="after")
    def valid_choice(self):
        if any(not v.strip() or len(v) > 200 for v in self.options) or len(set(self.options)) != len(self.options):
            raise ValueError("Invalid choices")
        if self.options and self.answer and self.answer not in self.options:
            raise ValueError("Invalid answer")
        return self

class TaskDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    title: str = Field(max_length=1000)
    owner: str = Field(max_length=200)
    recipient: str = Field(max_length=300)
    due: str = Field(max_length=100)
    uncertainty: str = Field(max_length=1000)
    id: str = Field(default="", max_length=100)
    included: bool = True
    suggested: bool = True
    ownerId: str = Field(default="", max_length=200)
    questions: list[Question] = Field(default_factory=list, max_length=8)

class Draft(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    summary: str = Field(max_length=64000)
    decisions: str = Field(max_length=8000)
    openQuestions: str = Field(max_length=8000)
    tasks: list[TaskDraft] = Field(max_length=40)
    people: list[Person] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def stable_ids_and_legacy(self):
        for i, task in enumerate(self.tasks):
            if not task.id:
                task.id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"task:{i}:{task.title}"))
            if task.uncertainty.strip() and not task.questions:
                # Legacy text is preserved, not rewritten into invented choices.
                field = "owner" if re.fullmatch(r"(?i)(zuständigkeit|verantwortliche person|person) (fehlt|unklar|nicht genannt)[.!]?|(owner|responsible person|person) (missing|unclear|not named)[.!]?", task.uncertainty.strip()) else "context"
                task.questions = [Question(label=task.uncertainty, field=field)]
            for j, question in enumerate(task.questions):
                if not question.id:
                    question.id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{task.id}:{j}:{question.label}"))
            if len({q.id for q in task.questions}) != len(task.questions):
                raise ValueError("Duplicate question ids")
        if len({t.id for t in self.tasks}) != len(self.tasks):
            raise ValueError("Duplicate task ids")
        if len({p.id for p in self.people}) != len(self.people):
            raise ValueError("Duplicate people")
        return self
