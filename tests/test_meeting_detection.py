"""Activity is a suggestion, never recording permission or proof of a call."""
import math

import pytest
from pydantic import ValidationError
from scribeloop_core.capture.clock import SessionClock
from scribeloop_core.meetings.contracts import (
    ActivitySnapshot,
    AppIdentity,
    CaptureSelection,
    ProcessActivity,
)
from scribeloop_core.meetings.detector import MeetingDetector

APPS = (
    AppIdentity(app_id='teams', name='Teams', kind='meeting',
                bundle_ids=('example.teams', 'example.teams.helper')),
    AppIdentity(app_id='slack', name='Slack', kind='meeting', bundle_ids=('example.slack',)),
    AppIdentity(app_id='browser', name='Browser', kind='browser', bundle_ids=('example.browser',)),
)


def process(bundle='example.teams', *, mic=True, output=True, pid=10):
    return ProcessActivity(pid=pid, bundle_id=bundle, input_running=mic, output_running=output)


def sample(now, *processes, available=True, complete=True):
    return ActivitySnapshot(observed_at=float(now), available=available,
                            complete=complete, processes=processes)


def test_idle_apps_and_unknown_processes_never_prompt():
    detector = MeetingDetector(APPS)
    for now in range(10):
        assert detector.update(sample(now, process(mic=False, output=False),
                                      process('example.teams.evil'))) == ()


def test_helpers_aggregate_and_id_stays_stable_during_mute_and_silence():
    detector = MeetingDetector(APPS)
    for now in range(4):
        rows = detector.update(sample(now, process(output=False),
                                      process('example.teams.helper', mic=False, pid=11)))
    assert len(rows) == 1
    candidate = rows[0]
    assert candidate.evidence == ('input_active', 'output_active')
    assert candidate.confidence == 'possible'
    muted = detector.update(sample(4, process(mic=False)))[0]
    assert muted.id == candidate.id
    assert muted.confidence == 'ambiguous'
    for now in range(5, 19):
        assert detector.update(sample(now))[0].id == candidate.id
    assert detector.update(sample(20)) == ()


def test_interrupted_start_does_not_accumulate_confirmation():
    detector = MeetingDetector(APPS)
    assert not detector.update(sample(0))
    assert not detector.update(sample(1, process()))
    assert not detector.update(sample(2, process(output=False)))
    assert not detector.update(sample(3, process()))
    assert len(detector.update(sample(4, process()))) == 1


@pytest.mark.parametrize('mic,output', [(True, False), (False, True)])
def test_single_direction_never_prompts(mic, output):
    detector = MeetingDetector(APPS)
    detector.update(sample(0, process(mic=mic, output=output)))
    assert detector.update(sample(3, process(mic=mic, output=output))) == ()


def test_browser_never_claims_a_specific_meeting_even_with_both_directions():
    detector = MeetingDetector(APPS)
    detector.update(sample(0, process('example.browser')))
    detector.update(sample(1, process('example.browser')))
    candidate, = detector.update(sample(3, process('example.browser')))
    assert candidate.kind == 'browser' and candidate.confidence == 'ambiguous'


def test_multiple_candidates_and_dismissal_until_new_episode():
    detector = MeetingDetector(APPS, end_grace_s=2, dismiss_cooldown_s=2)
    processes = (process(), process('example.slack'))
    detector.update(sample(0, *processes))
    detector.update(sample(1, *processes))
    rows = detector.update(sample(3, *processes))
    assert len(rows) == 2
    teams = next(row for row in rows if row.app_id == 'teams')
    assert detector.dismiss(teams.id)
    assert not detector.dismiss('stale-candidate')
    for now in range(4, 10):
        assert [row.app_id for row in detector.update(sample(now, *processes))] == ['slack']
    detector.update(sample(10))
    assert detector.update(sample(12)) == ()
    detector.update(sample(13, process()))
    fresh, = detector.update(sample(16, process()))
    assert fresh.id != teams.id
    assert not detector.dismiss(teams.id)


@pytest.mark.parametrize('failure', ['unavailable', 'partial', 'gap'])
def test_missing_observations_do_not_satisfy_debounce(failure):
    detector = MeetingDetector(APPS)
    detector.update(sample(0, process()))
    if failure == 'unavailable':
        detector.update(sample(2, available=False, complete=False))
    elif failure == 'partial':
        detector.update(sample(2, process(), complete=False))
    assert detector.update(sample(10, process())) == ()
    assert detector.update(sample(11, process())) == ()
    assert len(detector.update(sample(13, process()))) == 1


def test_observer_failure_does_not_end_or_redisplay_dismissed_episode():
    detector = MeetingDetector(APPS, end_grace_s=2)
    detector.update(sample(0, process()))
    detector.update(sample(1, process()))
    candidate, = detector.update(sample(3, process()))
    detector.dismiss(candidate.id)
    assert detector.update(sample(4, available=False, complete=False)) == ()
    assert not detector.available
    assert detector.update(sample(20, process())) == ()
    assert detector.available
    detector.update(sample(21))
    detector.update(sample(22, available=False, complete=False))
    detector.update(sample(30))  # Failure does not count toward end grace.
    assert 'teams' in detector._episodes
    detector.update(sample(32))
    assert 'teams' not in detector._episodes


def test_unknown_activity_never_counts_as_idle():
    detector = MeetingDetector(APPS, end_grace_s=2)
    detector.update(sample(0, process()))
    detector.update(sample(1, process()))
    candidate, = detector.update(sample(3, process()))
    for now in range(4, 10):
        rows = detector.update(sample(now, process(mic=None, output=False)))
        assert rows[0].id == candidate.id


def test_clock_order_config_and_mapping_validation():
    detector = MeetingDetector(APPS)
    detector.update(sample(1))
    with pytest.raises(ValueError, match='increasing'):
        detector.update(sample(1))
    for value in (0, -1, math.inf, math.nan, True):
        with pytest.raises(ValueError):
            MeetingDetector(APPS, confirmation_samples=value)
    with pytest.raises(ValueError, match='Duplicate'):
        MeetingDetector(APPS + (APPS[0],))
    with pytest.raises(ValueError, match='ambiguous'):
        MeetingDetector(APPS + (AppIdentity(app_id='other', name='Other', kind='meeting',
                                            bundle_ids=('example.teams',)),))


def test_explicit_capture_scope_and_mic_are_required():
    assert CaptureSelection(scope='application', bundle_ids=('example.teams',),
                            microphone_device_id='mic').scope == 'application'
    assert CaptureSelection(scope='system', microphone_device_id='mic').bundle_ids == ()
    for fields in (
        {'scope': 'application', 'microphone_device_id': 'mic'},
        {'scope': 'system', 'bundle_ids': ('example.teams',), 'microphone_device_id': 'mic'},
        {'scope': 'system'},
        {'scope': 'application', 'bundle_ids': ('',), 'microphone_device_id': 'mic'},
        {'scope': 'application', 'bundle_ids': ('-x',), 'microphone_device_id': 'mic'},
    ):
        with pytest.raises(ValidationError):
            CaptureSelection(**fields)


def test_shared_clock_preserves_offsets_gaps_and_large_host_times():
    clock = SessionClock(10**18)
    assert clock.milliseconds(10**18) == 0
    assert clock.milliseconds(10**18 + 150_000_000) == 150
    assert clock.milliseconds(10**18 + 3_250_000_000) == 3250
    for bad in (10**18 - 1, True, 1.5):
        with pytest.raises(ValueError):
            clock.milliseconds(bad)
    with pytest.raises(ValueError):
        SessionClock(-1)


@pytest.mark.parametrize('app', ['example.teams','example.slack','example.browser'])
def test_repeated_chat_notification_output_never_becomes_a_call(app):
    detector=MeetingDetector(APPS,)
    for now in range(120):
        # Notification sounds, including sustained output-stream activity.
        assert not detector.update(sample(now,process(app,mic=False,output=now%20<8)))
    assert not detector._episodes


def test_known_microphone_edge_confirms_without_fixed_six_second_wait():
    detector = MeetingDetector(APPS)
    assert not detector.update(sample(0))
    assert not detector.update(sample(1, process()))
    assert len(detector.update(sample(2, process()))) == 1


def test_microphone_edge_can_precede_remote_audio():
    detector = MeetingDetector(APPS)
    detector.update(sample(0))
    for now in range(1, 6):
        assert not detector.update(sample(now, process(output=False)))
    assert not detector.update(sample(6, process()))
    assert len(detector.update(sample(7, process()))) == 1


def test_already_held_microphone_plus_notifications_does_not_prompt():
    detector = MeetingDetector(APPS)
    for now in range(30):
        assert not detector.update(sample(now, process(output=now % 10 >= 5)))


def test_startup_and_browser_require_three_confirmations():
    for bundle in ('example.teams', 'example.browser'):
        detector = MeetingDetector(APPS)
        assert not detector.update(sample(0, process(bundle)))
        assert not detector.update(sample(1, process(bundle)))
        assert len(detector.update(sample(2, process(bundle)))) == 1


def test_dismissal_survives_churn_but_new_known_call_bypasses_quiet_period():
    detector = MeetingDetector(APPS, end_grace_s=2)
    for now in range(3):
        rows = detector.update(sample(now, process()))
    candidate, = rows
    detector.dismiss(candidate.id)
    detector.reconfigure(tuple(reversed(APPS)))
    assert not detector.update(sample(3, process()))
    detector.update(sample(4))
    detector.update(sample(6))
    assert not detector.update(sample(7, process()))
    fresh, = detector.update(sample(8, process()))
    assert fresh.id != candidate.id


def test_uncertain_reappearance_stays_suppressed_during_quiet_period():
    detector = MeetingDetector(APPS, end_grace_s=2)
    for now in range(3):
        rows = detector.update(sample(now, process()))
    detector.dismiss(rows[0].id)
    detector.update(sample(3))
    detector.update(sample(5))
    detector.update(sample(6, available=False, complete=False))
    for now in range(7, 12):
        assert not detector.update(sample(now, process()))


def test_direction_loss_does_not_mark_a_running_call_inactive():
    detector=MeetingDetector(APPS)
    detector.update(sample(0,process()))
    detector.update(sample(3,process()))
    detector.update(sample(4,process(mic=False)))
    assert detector.activity['teams']=='active'
