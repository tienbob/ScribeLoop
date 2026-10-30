"""API language union and the narrower Nova-3 choices; each backend validates its set."""
from typing import Annotated, Literal

from pydantic import AfterValidator

LanguageChoice = Literal['auto','en','es','fr','de','hi','ru','pt','ja','it','nl','vi','zh']
AUTO_LANGUAGES = ('en','vi')
LEGACY_DESKTOP_LANGUAGES = frozenset(('es','fr','de','hi','ru','pt','ja','it','nl'))
LANGUAGE_NAMES = {'en':'English','es':'Spanish','fr':'French','de':'German','hi':'Hindi',
    'ru':'Russian','pt':'Portuguese','ja':'Japanese','it':'Italian','nl':'Dutch','vi':'Tiếng Việt','zh':'Chinese'}
HINT_LANGUAGE_NAMES = {
    'af':'Afrikaans','ar':'Arabic','be':'Belarusian','bn':'Bengali','bs':'Bosnian',
    'bg':'Bulgarian','ca':'Catalan','zh-HK':'Chinese · Cantonese','zh':'Chinese · Mandarin',
    'hr':'Croatian','cs':'Czech','da':'Danish','nl':'Dutch','en':'English','et':'Estonian',
    'fi':'Finnish','nl-BE':'Flemish','fr':'French','gl':'Galician','de':'German','el':'Greek',
    'gu':'Gujarati','he':'Hebrew','hi':'Hindi','hu':'Hungarian','id':'Indonesian','it':'Italian',
    'ja':'Japanese','kn':'Kannada','ko':'Korean','lv':'Latvian','lt':'Lithuanian',
    'mk':'Macedonian','ms':'Malay','mr':'Marathi','no':'Norwegian','fa':'Persian',
    'pl':'Polish','pt':'Portuguese','ro':'Romanian','ru':'Russian','sr':'Serbian',
    'sk':'Slovak','sl':'Slovenian','es':'Spanish','sv':'Swedish','tl':'Tagalog','ta':'Tamil',
    'te':'Telugu','th':'Thai','tr':'Turkish','uk':'Ukrainian','vi':'Tiếng Việt','xh':'Xhosa','zu':'Zulu'}
LANGUAGE_NAMES.update(HINT_LANGUAGE_NAMES)
_HINT_CODES = {code.lower():code for code in HINT_LANGUAGE_NAMES}
LANGUAGE_CHOICES = frozenset(('auto',*AUTO_LANGUAGES,'vi'))


def validate_language_hints(value):
    if not isinstance(value,list) or not 1 <= len(value) <= len(HINT_LANGUAGE_NAMES):
        raise ValueError('Select at least one supported language')
    if any(not isinstance(code,str) or code not in HINT_LANGUAGE_NAMES for code in value):
        raise ValueError('Unsupported language hint')
    return list(dict.fromkeys(value))


LanguageHints = Annotated[list[str], AfterValidator(validate_language_hints)]


def deepgram_language_options(language, hints=None):
    """Bilingual hints bias Nova-3; they are not a hard language allowlist."""
    if hints is not None:
        return {'language':'multi','language_hint':validate_language_hints(hints)}
    if language in {'auto','multi'}:
        return {'language':'multi','language_hint':list(AUTO_LANGUAGES)}
    if language not in AUTO_LANGUAGES:
        raise ValueError('Choose English, Vietnamese, or English + Vietnamese')
    return {'language':language}


def word_language(value):
    if isinstance(value,str):
        matched = _HINT_CODES.get(value.lower())
        if matched:
            return matched
        code = value.lower().split('-')[0]
        if code in HINT_LANGUAGE_NAMES:
            return code
    return None
