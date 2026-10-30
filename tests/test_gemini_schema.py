"""Gemini grammar size and local validation are deliberately separate contracts."""
import json

import pytest
from scribeloop_core.llm.grounded import validate_summary
from scribeloop_core.llm.schemas import GroundedSummary
from scribeloop_core.llm.summary_provider import gemini_summary_schema


def test_provider_grammar_has_no_nested_bounds_defaults_or_references():
    schema=gemini_summary_schema()
    encoded=json.dumps(schema)
    for key in ('$ref','$defs','minItems','maxItems','minLength','maxLength','default'):
        assert f'"{key}"' not in encoded
    assert schema['additionalProperties'] is False
    assert schema['properties']['title']['anyOf'][0]['properties']['text']['type']=='string'
    assert set(schema['required'])==set(GroundedSummary.model_fields)
    action=schema['properties']['action_items']['items']
    assert set(action['required'])=={'task','owner','due','segment_ids'}
    assert action['properties']['owner']['anyOf'][1]['type']=='null'


def test_local_validator_still_rejects_excess_items_and_unknown_citations():
    item={'text':'Synthetic overview','segment_ids':['s']}
    value={'title':None,'overview':item,'key_points':[],'decisions':[],
           'action_items':[],'open_questions':[],'uncertainties':[]}
    assert validate_summary(value,[{'id':'s'}])['overview']==item
    for bad in ({**value,'key_points':[item]*21},
                {**value,'overview':{**item,'segment_ids':[]}},
                {**value,'overview':{**item,'segment_ids':['foreign']}}):
        with pytest.raises(ValueError): validate_summary(bad,[{'id':'s'}])
