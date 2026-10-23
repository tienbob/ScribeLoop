"""API language union and the narrower Nova-3 choices; each backend validates its set."""
from typing import Literal

LanguageChoice = Literal['auto','en','es','fr','de','hi','ru','pt','ja','it','nl','vi','zh']
AUTO_LANGUAGES = ('en','es','fr','de','hi','ru','pt','ja','it','nl')
LANGUAGE_NAMES = {'en':'English','es':'Spanish','fr':'French','de':'German','hi':'Hindi',
    'ru':'Russian','pt':'Portuguese','ja':'Japanese','it':'Italian','nl':'Dutch','vi':'Tiếng Việt','zh':'Chinese'}
LANGUAGE_CHOICES = frozenset(('auto',*AUTO_LANGUAGES,'vi'))


def word_language(value):
    if isinstance(value,str):
        code = value.lower().split('-')[0]
        if code in AUTO_LANGUAGES:
            return code
    return None
