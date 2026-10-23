"""Documented Nova-3 language choices; no unsupported language-hint parameters."""
from typing import Literal

LanguageChoice = Literal['auto','en','es','fr','de','hi','ru','pt','ja','it','nl','vi']
AUTO_LANGUAGES = ('en','es','fr','de','hi','ru','pt','ja','it','nl')
LANGUAGE_NAMES = {'en':'English','es':'Spanish','fr':'French','de':'German','hi':'Hindi',
    'ru':'Russian','pt':'Portuguese','ja':'Japanese','it':'Italian','nl':'Dutch','vi':'Tiếng Việt'}
LANGUAGE_CHOICES = frozenset(('auto',*LANGUAGE_NAMES))


def word_language(value):
    if isinstance(value,str):
        code = value.lower().split('-')[0]
        if code in AUTO_LANGUAGES:
            return code
    return None
