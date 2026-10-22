"""Discovery UI state stays ephemeral, fresh, and independent of recording."""
import pytest
from scribeloop_core.meetings.contracts import ActivitySnapshot, AppIdentity, ProcessActivity
from scribeloop_core.meetings.service import DiscoveryService

APP = AppIdentity(app_id='example.teams', name='Teams <literal>', kind='meeting',
                  bundle_ids=('example.teams', 'example.teams.helper'))


class Clock:
    now = 0.0

    def __call__(self):
        return self.now


class Observer:
    available = True
    complete = True
    active = True
    error = False

    def __init__(self, clock):
        self.clock = clock

    def snapshot(self):
        if self.error:
            raise OSError('private provider path')
        rows = (ProcessActivity(pid=123, bundle_id='example.teams.helper',
                                input_running=True, output_running=True),)
        return ActivitySnapshot(observed_at=self.clock(), available=self.available,
                                complete=self.complete if self.available else False,
                                processes=rows if self.active and self.available else ())


async def setup_service():
    clock = Clock()
    observer = Observer(clock)
    events = []
    apps = [APP]

    async def publish(event):
        events.append(event)

    service = DiscoveryService(publish, observer=observer, apps_provider=lambda: tuple(apps),
                               clock=clock, poll_s=3600)
    await service.refresh()
    clock.now = 3.0
    await service.refresh()
    return service, clock, observer, apps, events


async def test_fresh_candidates_are_scoped_and_metadata_only():
    service, clock, _, _, events = await setup_service()
    state = service.snapshot()
    assert state['status'] == 'available' and state['recording_enabled'] is False
    candidate, = state['candidates']
    assert candidate['selectable'] and candidate['id'].endswith(':example.teams:1')
    assert 'pid' not in str(state) and 'bundle_ids' not in str(state)
    selection = await service.select(scope='application', app_id=APP.app_id,
                                     microphone_id='default', candidate_id=candidate['id'])
    assert selection['name'] == APP.name and selection['recording_enabled'] is False
    clock.now = 4.0
    await service.refresh()
    assert len(events) == 2, 'Do not flood clients with unchanged timestamps'


async def test_unavailable_observer_keeps_manual_selection_and_dismissal():
    service, clock, observer, _, _ = await setup_service()
    candidate, = service.snapshot()['candidates']
    clock.now = 4.0
    observer.error = True
    await service.refresh()
    assert service.snapshot()['status'] == 'unavailable'
    assert service.snapshot()['candidates'][0]['selectable'] is False
    with pytest.raises(ValueError):
        await service.select(scope='application', app_id=APP.app_id,
                             microphone_id='default', candidate_id=candidate['id'])
    assert (await service.select(scope='application', app_id=APP.app_id,
                                  microphone_id='default'))['app_id'] == APP.app_id
    await service.dismiss(candidate['id'])
    assert service.snapshot()['candidates'] == []
    observer.error = False
    clock.now = 5.0
    await service.refresh()
    assert service.snapshot()['candidates'] == []


async def test_expired_wrong_app_and_wrong_incarnation_are_rejected():
    service, clock, _, _, _ = await setup_service()
    candidate, = service.snapshot()['candidates']
    other, *_ = await setup_service()
    assert other.snapshot()['candidates'][0]['id'] != candidate['id']
    with pytest.raises(ValueError):
        await other.dismiss(candidate['id'])
    clock.now = 9.0
    assert service.snapshot()['status'] == 'unavailable'
    with pytest.raises(ValueError):
        await service.select(scope='application', app_id=APP.app_id,
                             microphone_id='default', candidate_id=candidate['id'])
    assert (await service.select(scope='system', app_id=None,
                                  microphone_id='default'))['scope'] == 'system'
    for fields in (
        {'scope': 'application', 'app_id': 'unknown', 'microphone_id': 'default'},
        {'scope': 'system', 'app_id': APP.app_id, 'microphone_id': 'default'},
        {'scope': 'system', 'app_id': None, 'microphone_id': 'arbitrary-device'},
    ):
        with pytest.raises(ValueError):
            await service.select(**fields)


async def test_installation_removal_invalidates_candidates_before_selection():
    service, _, _, apps, _ = await setup_service()
    candidate, = service.snapshot()['candidates']
    apps.clear()
    with pytest.raises(ValueError):
        await service.select(scope='application', app_id=APP.app_id,
                             microphone_id='default', candidate_id=candidate['id'])
    assert service.snapshot()['apps'] == [] and service.snapshot()['candidates'] == []


async def test_registry_failure_rejects_selection_without_leaking_provider_error():
    service, clock, _, _, _ = await setup_service()

    def broken():
        raise OSError('/private/app/location')

    service._apps_provider = broken
    clock.now = 4.0
    await service.refresh()
    assert service.snapshot()['status'] == 'unavailable'
    with pytest.raises(ValueError, match='Cannot verify') as error:
        await service.select(scope='application', app_id=APP.app_id, microphone_id='default')
    assert '/private' not in str(error.value)


async def test_disabled_discovery_keeps_installed_manual_choices():
    events = []

    async def publish(event):
        events.append(event)

    service = DiscoveryService(publish, apps_provider=lambda: (APP,))
    await service.start()
    assert service.snapshot()['status'] == 'disabled'
    assert service.snapshot()['apps'][0]['id'] == APP.app_id
    assert (await service.select(scope='application', app_id=APP.app_id,
                                  microphone_id='default'))['app_id'] == APP.app_id
    await service.close()


async def test_background_task_shutdown():
    clock = Clock()

    async def publish(event):
        pass

    service = DiscoveryService(publish, observer=Observer(clock), clock=clock)
    await service.start()
    task = service._task
    await service.close()
    assert task.done() and service._task is None
