"""API language union and the narrower Nova-3 choices; each backend validates its set."""
from typing import Literal

LanguageChoice = Literal['auto','en','es','fr','de','hi','ru','pt','ja','it','nl','vi','zh']
AUTO_LANGUAGES = ('en','vi')
LEGACY_DESKTOP_LANGUAGES = frozenset(('es','fr','de','hi','ru','pt','ja','it','nl'))
LANGUAGE_NAMES = {'en':'English','es':'Spanish','fr':'French','de':'German','hi':'Hindi',
    'ru':'Russian','pt':'Portuguese','ja':'Japanese','it':'Italian','nl':'Dutch','vi':'Tiếng Việt','zh':'Chinese'}
LANGUAGE_CHOICES = frozenset(('auto',*AUTO_LANGUAGES,'vi'))


def deepgram_language_options(language):
    """Bilingual hints bias Nova-3; they are not a hard language allowlist."""
    if language in {'auto','multi'}:
        return {'language':'multi','language_hint':list(AUTO_LANGUAGES)}
    if language not in AUTO_LANGUAGES:
        raise ValueError('Choose English, Vietnamese, or English + Vietnamese')
    return {'language':language}


def word_language(value):
    if isinstance(value,str):
        code = value.lower().split('-')[0]
        if code in AUTO_LANGUAGES:
            return code
    return None
