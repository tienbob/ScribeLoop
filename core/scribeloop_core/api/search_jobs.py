"""Cancellable retrieval jobs; the WebSocket command reader never awaits providers."""
from __future__ import annotations

import asyncio
import time

import httpx

from ..async_compat import timeout
from ..storage.search_work import SearchAuthorizationError, SearchWork

SEARCH_DEADLINE_S = 120


def search_failure(error):
    if isinstance(error,ValueError) and str(error)=='Enter from 1 to 200 search characters':
        return 'Search supports up to 200 characters. Shorten your query; your filters are kept.'
    if isinstance(error,(TimeoutError,httpx.TimeoutException)):
        return 'Search timed out. Your query and filters are kept. Try again or narrow the meeting filters.'
    if isinstance(error,httpx.HTTPStatusError):
        if error.response.status_code == 429:
            return 'The embedding provider quota or rate limit was reached. Check your quota, then retry.'
        if error.response.status_code in {401,403}:
            return 'The embedding provider rejected access. Check its key and model in Settings.'
    return 'Search could not finish. Check the filters and embedding settings, then retry. Saved conversations are unchanged.'


def cancel_search(task):
    task.search_cancelled.set()
    task.cancel()


async def run_search(*, app, desktop, command, provider, request_id, queue, lock, summary_id, cancelled):
    async def progress(stage, completed, total):
        await queue.put({'type':'search_progress','response_to':request_id,
                         'stage':stage,'completed':completed,'total':total})
    authorize=app.state.store.search_authorization()
    deadline=time.monotonic()+SEARCH_DEADLINE_S
    def check():
        if cancelled.is_set():
            raise SearchAuthorizationError('Search cancelled')
        if time.monotonic()>=deadline:
            raise TimeoutError('Search deadline exceeded')
        authorize()
    work=SearchWork(authorize=check,progress=progress)
    try:
        async with timeout(SEARCH_DEADLINE_S):
            await work.report('waiting')
            async with lock:
                work.check()
                recorder=app.state.recording
                result=await desktop.read(app,command,provider,
                    recorder.active_id if recorder else None,
                    summary_id(),work=work)
                work.check()
                await queue.put({**result,'response_to':request_id})
                await queue.put(desktop.receipt(request_id,'accepted'))
    except (asyncio.CancelledError,SearchAuthorizationError) as error:
        # Never block control commands on a disconnected or slow consumer.
        desktop.receipt(request_id,'cancelled')
        if not queue.full():
            queue.put_nowait({'type':'search_cancelled','response_to':request_id})
        if isinstance(error,asyncio.CancelledError):
            raise
    except Exception as error:  # noqa: BLE001 - do not expose provider bodies/keys
        desktop.receipt(request_id,'rejected')
        if not queue.full():
            queue.put_nowait({'type':'search_error','response_to':request_id,
                             'message':search_failure(error)})
