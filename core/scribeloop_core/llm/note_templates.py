"""Bounded note preferences; canonical cited-summary fields remain unchanged."""
from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SectionKey = Literal['overview', 'key_points', 'decisions', 'action_items', 'open_questions', 'uncertainties']
SECTION_LABELS = {
    'overview': ('At a glance', 'Tổng quan'),
    'key_points': ('Key points', 'Nội dung chính'),
    'decisions': ('Decisions', 'Quyết định'),
    'action_items': ('Next steps', 'Việc cần làm'),
    'open_questions': ('Open questions', 'Câu hỏi còn mở'),
    'uncertainties': ('Uncertainties', 'Điểm chưa rõ'),
}


class TemplateSection(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    key: SectionKey
    label: str = Field(min_length=1, max_length=80)
    label_vi: str | None = Field(default=None, min_length=1, max_length=80)

    @field_validator('label', 'label_vi')
    @classmethod
    def nonblank(cls, value):
        if value is not None and not value.strip():
            raise ValueError('Section labels must not be blank')
        return value


class NoteTemplate(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    name: str = Field(min_length=1, max_length=80)
    focus: str = Field(default='', max_length=2000)
    sections: list[TemplateSection] = Field(min_length=1, max_length=6)

    @field_validator('name')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('Template name must not be blank')
        return value

    @field_validator('sections')
    @classmethod
    def canonical_sections(cls, value):
        keys = [section.key for section in value]
        if len(set(keys)) != len(keys) or 'overview' not in keys:
            raise ValueError('Sections must be unique and include overview')
        return value

    def presentation(self, language='en'):
        """Never hide supported facts because a custom layout omits their field."""
        labels = {section.key: section for section in self.sections}
        keys = list(labels) + [key for key in SECTION_LABELS if key not in labels]
        return [{'key': key, 'label':
                 (labels[key].label_vi or labels[key].label) if language == 'vi' and key in labels
                 else labels[key].label if key in labels else SECTION_LABELS[key][language == 'vi']}
                for key in keys]


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def definition_hash(definition):
    template = NoteTemplate.model_validate(definition)
    return hashlib.sha256(canonical_json(template.model_dump()).encode()).hexdigest()


def _builtin(name, focus, overrides=None):
    overrides = overrides or {}
    return NoteTemplate(name=name, focus=focus, sections=[
        TemplateSection(key=key, label=overrides.get(key, pair)[0], label_vi=overrides.get(key, pair)[1])
        for key, pair in SECTION_LABELS.items()])


BUILTINS = {
    'general': _builtin('General', 'Summarize the main discussion, supported decisions, explicit actions and unresolved questions.'),
    'stand-up': _builtin('Stand-up', 'Focus on stated updates, blockers and explicit next steps. Do not infer progress or completion.',
                        {'key_points': ('Updates and blockers', 'Cập nhật và trở ngại')}),
    'customer-call': _builtin('Customer call', 'Focus on stated customer needs, commitments and next steps. Never invent budget, owners or purchase intent.',
                             {'key_points': ('Customer needs and discussion', 'Nhu cầu và trao đổi với khách hàng')}),
    'interview': _builtin('Interview', 'Summarize discussion themes and evidence stated by participants. Do not infer a hiring verdict, suitability or personal traits.',
                         {'key_points': ('Discussion and evidence', 'Nội dung trao đổi và dẫn chứng')}),
    'planning': _builtin('Planning', 'Separate proposals from approved decisions. Highlight stated dependencies and explicitly assigned actions.',
                        {'key_points': ('Proposals and dependencies', 'Đề xuất và phụ thuộc')}),
    'one-to-one': _builtin('One-to-one', 'Focus on discussion themes, agreed follow-ups and unresolved questions. Do not infer sentiment, intent or performance.',
                          {'key_points': ('Discussion themes', 'Chủ đề trao đổi')}),
}
