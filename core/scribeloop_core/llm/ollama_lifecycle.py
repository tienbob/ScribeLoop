"""Local Ollama memory lifecycle: small models unload per task; big ones on quit or idle.

Ollama keeps the most recently used model resident, which holds memory after the
app no longer needs it. Policy: models below 8B parameters are unloaded as soon
as their task (transcription, cleanup or notes) completes; larger or unknown
models stay resident with a 15-minute keep-alive and are unloaded when the app
quits. Unload requests go only to loopback endpoints and are best-effort: the
saved transcript and summaries never depend on a successful unload, and a
crashed app still frees memory through the keep-alive.
"""
from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import logging
import re
from urllib.parse import urlsplit

import httpx

KEEP_ALIVE = '15m'
SMALL_MODEL_B = 8.0
UNLOAD_TIMEOUT_S = 4.0
RELEASE_ALL_TIMEOUT_S = 5.0

log = logging.getLogger(__name__)


def native_root(base_url):
    """Ollama native API root for an OpenAI-compatible base URL."""
    return base_url.rstrip('/').removesuffix('/v1')


def loopback(base_url):
    url = urlsplit(base_url or '')
    try:
        return ipaddress.ip_address(url.hostname or '').is_loopback
    except ValueError:
        return url.hostname == 'localhost'


def parameter_count_b(show):
    """Billions of parameters from /api/show metadata, or None when unknown."""
    if not isinstance(show,dict):
        return None
    info = show.get('model_info')
    count = info.get('general.parameter_count') if isinstance(info,dict) else None
    if isinstance(count,(int,float)) and count > 0:
        return count/1e9
    size = (show.get('details') or {}).get('parameter_size','') if isinstance(show.get('details'),dict) else ''
    match = re.search(r'([0-9]+(?:\.[0-9]+)?)\s*(b|m)\b',str(size),re.IGNORECASE)
    if match is None:
        return None
    return float(match.group(1))*(0.001 if match.group(2).lower() == 'm' else 1)


def _client(base_url,*,client_factory,timeout_s):
    return (client_factory or httpx.AsyncClient)(timeout=httpx.Timeout(timeout_s,connect=2),
        trust_env=False,follow_redirects=False)


async def model_size_b(base_url,model,*,client_factory=None):
    """Parameter size in billions from the local server, or None when unknown."""
    if not loopback(base_url) or not model:
        return None
    try:
        async with _client(base_url,client_factory=client_factory,timeout_s=4) as client:
            response = await client.post(native_root(base_url)+'/api/show',json={'model':model})
        if response.status_code != 200:
            return None
        return parameter_count_b(response.json())
    except (httpx.HTTPError,ValueError,OSError):
        return None


async def release(base_url,model,*,client_factory=None):
    """Ask a loopback Ollama to unload this model now; best-effort and idempotent."""
    if not loopback(base_url) or not model:
        return False
    try:
        async with _client(base_url,client_factory=client_factory,
                           timeout_s=UNLOAD_TIMEOUT_S) as client:
            response = await client.post(native_root(base_url)+'/api/generate',
                json={'model':model,'keep_alive':0})
        return response.status_code == 200
    except (httpx.HTTPError,OSError):
        return False


async def release_if_small(base_url,model,*,client_factory=None):
    """Unload the model only when its size is known and below the small threshold."""
    size = await model_size_b(base_url,model,client_factory=client_factory)
    if size is None or size >= SMALL_MODEL_B:
        return False
    return await release(base_url,model,client_factory=client_factory)


def session_targets(summary_provider, recording):
    """Loopback Ollama models one app session may hold: notes, cleanup, local ASR."""
    targets = []
    if summary_provider is not None and getattr(summary_provider,'provider','') == 'Ollama' \
            and not getattr(summary_provider,'cloud',True):
        targets.append((summary_provider.base_url,summary_provider.model))
    cleanup = getattr(recording,'cleanup_provider',None)
    if cleanup is not None and getattr(cleanup,'provider','') == 'Ollama' \
            and not getattr(cleanup,'cloud',True):
        targets.append((cleanup.base_url,cleanup.model))
    runner = (getattr(recording,'runners',{}) or {}).get('ollama')
    config = getattr(runner,'config',None)
    if config is not None and getattr(config,'configured',False):
        targets.append((config.base_url,config.model))
    return targets


async def release_all(targets,*,client_factory=None):
    """Unload several models concurrently with one bounded deadline; never raises."""
    pending = {(native_root(base_url),model) for base_url,model in targets
               if loopback(base_url) and model}
    if not pending:
        return
    async def run(base_url,model):
        with contextlib.suppress(Exception):
            await release(base_url,model,client_factory=client_factory)
    try:
        async with asyncio.timeout(RELEASE_ALL_TIMEOUT_S):
            await asyncio.gather(*(run(base_url,model) for base_url,model in sorted(pending)))
    except Exception as exc:  # noqa: BLE001 - quitting must never fail or wait on an unload
        log.debug('Ollama release unfinished (%s)',type(exc).__name__)
