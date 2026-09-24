"""Storage policy and unattended delivery use simulated Graph only."""
import asyncio
import copy
import threading
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests
from engine.meeting_notes import Notes, Review
from engine.speech.core import Transcript
from test_notes_onenote import setup, choose, FakeGraph, ACCOUNT, BOOK, PAGES, PAGE

OWN = frozenset({'own.example'})

def fresh(notes, old, *, live=False, domain='contoso.com'):
    identity = str(uuid.uuid4())
    review = Review(SimpleNamespace(id=identity, transcript=Transcript(identity, ''), stop=lambda: None),
                    'Nächster Termin', '2026-09-17T10:00:00', {})
    review.people = [dict(id='p', name='Customer', email='p@' + domain, source='calendar')]
    review.onenote.update(autoSave=True, selection='default')
    if not live:
        review.ended, review.phase = '2026-09-17T11:00:00', 'complete'
        review.draft = copy.deepcopy(old.draft)
    notes.reviews[review.id] = review
    notes.persist(review)
    return review

async def tick(notes):
    notes.tick(OWN)
    if notes.onenote.pending:
        await asyncio.gather(*list(notes.onenote.pending.values()))
    await asyncio.sleep(0)

def test_without_rule_or_user_input_finishes_locally(setup):
    notes, original = setup
    review = fresh(notes, original)
    asyncio.run(tick(notes))
    assert review.onenote['mode'] == 'local' and review.onenote['finalized']
    assert Path(review.document_path).is_file()
    assert not FakeGraph.created

def test_matching_rule_after_restart_saves_without_click(setup):
    notes, original = setup
    choose(notes, original)
    # Do not send the original in the watchdog used for this test.
    original.onenote['autoSave'] = False
    notes.persist(original)
    restarted = Notes(notes.folder)
    review = fresh(restarted, original, live=True)
    async def run():
        await tick(restarted)
        assert review.onenote['mode'] == 'onenote'
        assert review.onenote['target']['sectionId'] == 'section'
        assert not FakeGraph.created
        review.ended, review.phase, review.draft = '2026-09-17T11:00:00', 'complete', copy.deepcopy(original.draft)
        restarted.persist(review)
        await tick(restarted)
        await tick(restarted)
    asyncio.run(run())
    assert review.onenote['status'] == 'saved' and len(FakeGraph.created) == 1
    assert not review.document_path

@pytest.mark.parametrize('mode', ['unrelated', 'same', 'conflict', 'other-account'])
def test_default_only_uses_unambiguous_rules_for_current_account(setup, monkeypatch, mode):
    notes, original = setup
    choose(notes, original)
    original.onenote['autoSave'] = False
    prefs = notes.onenote.preferences['accounts'][ACCOUNT]
    review = fresh(notes, original, domain='other.example' if mode == 'unrelated' else 'contoso.com')
    if mode in ('same', 'conflict'):
        review.people.append(dict(id='p2', name='Other', email='p@other.example', source='calendar'))
        prefs['domains']['other.example'] = copy.deepcopy(prefs['domains']['contoso.com'])
        if mode == 'conflict': prefs['domains']['other.example']['sectionId'] = 'different-section'
    if mode == 'other-account': monkeypatch.setattr(FakeGraph, 'account', 'other-account')
    asyncio.run(tick(notes))
    assert review.onenote['mode'] == ('onenote' if mode == 'same' else 'local')
    assert len(FakeGraph.created) == (1 if mode == 'same' else 0)
    if mode == 'conflict': assert 'Mehrere' in review.onenote['notice']

def test_rule_keeps_its_section_when_another_meeting_uses_same_book(setup, monkeypatch):
    notes, original = setup
    choose(notes, original)
    original.onenote['autoSave'] = False
    notes.onenote.sections[(ACCOUNT, BOOK)].append(dict(id='archive', name='Archiv', pagesUrl=PAGES))
    other = fresh(notes, original, domain='other.example')
    notes.onenote.select(other, dict(mode='onenote', account=ACCOUNT, book=BOOK, section='archive', rememberDomain='other.example'), OWN)
    other.onenote['autoSave'] = False
    review = fresh(notes, original)
    asyncio.run(tick(notes))
    assert review.onenote['target']['sectionId'] == 'section'

@pytest.mark.parametrize('failure', ['deleted', 'forbidden', 'offline'])
def test_missing_or_unreachable_rule_falls_back_to_pc_with_notice(setup, monkeypatch, failure):
    notes, original = setup
    choose(notes, original); original.onenote['autoSave'] = False
    def sections(*args):
        if failure == 'offline': raise requests.Timeout()
        if failure == 'forbidden':
            from engine.notes_onenote import GraphReadError
            raise GraphReadError(403)
        return []
    monkeypatch.setattr(FakeGraph, 'sections', sections)
    review = fresh(notes, original)
    asyncio.run(tick(notes))
    assert review.onenote['mode'] == 'local' and review.onenote['notice']
    assert Path(review.document_path).exists() and not FakeGraph.created
    assert 'contoso.com' in notes.onenote.preferences['accounts'][ACCOUNT]['domains']

def test_manual_pc_choice_beats_rule_without_forgetting_it(setup):
    notes, original = setup
    choose(notes, original); original.onenote['autoSave'] = False
    review = fresh(notes, original)
    notes.onenote.select(review, {'mode': 'local'}, OWN)
    asyncio.run(tick(notes))
    assert review.onenote['mode'] == 'local' and not FakeGraph.created
    assert notes.onenote.preferences['accounts'][ACCOUNT]['domains']['contoso.com']['sectionId'] == 'section'

def test_rule_changes_never_move_an_already_local_meeting(setup):
    notes, original = setup
    review = fresh(notes, original)
    asyncio.run(tick(notes))
    choose(notes, original); original.onenote['autoSave'] = False
    asyncio.run(tick(notes))
    assert review.onenote['mode'] == 'local' and not FakeGraph.created

@pytest.mark.parametrize('fail', ['reject', 'timeout'])
def test_failure_retains_md_across_restart_and_does_not_repeat_post(setup, monkeypatch, fail):
    notes, review = setup
    document = Path(review.document_path)
    choose(notes, review)
    monkeypatch.setattr(FakeGraph, 'response', 403 if fail == 'reject' else 201)
    monkeypatch.setattr(FakeGraph, 'fail', fail == 'timeout')
    asyncio.run(tick(notes))
    assert document.exists() and review.onenote['error']
    restarted = Notes(notes.folder)
    asyncio.run(tick(restarted))
    assert document.exists() and len(FakeGraph.created) == 1
    if fail == 'timeout':
        monkeypatch.setattr(FakeGraph, 'found', PAGE)
        asyncio.run(restarted.onenote.publish(restarted.reviews[review.id], review.revision))
        assert not document.exists() and len(FakeGraph.created) == 1

def test_selecting_while_live_schedules_only_after_final_summary(setup):
    notes, review = setup
    review.ended, review.phase = '', 'live'
    choose(notes, review)
    async def run():
        await tick(notes)
        assert not FakeGraph.created
        review.ended, review.phase, review.busy = '2026-09-16T11:00:00', 'complete', True
        await tick(notes)
        assert not FakeGraph.created
        review.draft['summary'] = 'Finaler Stand'
        review.busy = False
        await tick(notes)
    asyncio.run(run())
    assert len(FakeGraph.created) == 1 and 'Finaler Stand' in FakeGraph.created[0][1]

def test_transfer_locks_edits_and_target_before_auth_yields(setup, monkeypatch):
    notes, review = setup
    choose(notes, review)
    started, release = threading.Event(), threading.Event()
    def sections(*args):
        started.set(); assert release.wait(5)
        return [{'id': 'section', 'name': 'Meetings', 'pagesUrl': PAGES}]
    monkeypatch.setattr(FakeGraph, 'sections', sections)
    async def run():
        task = asyncio.create_task(notes.onenote.publish(review, review.revision))
        try:
            assert await asyncio.to_thread(started.wait, 5)
            assert review.onenote['status'] == 'preparing'
            assert not review.public()['editable'] and not review.public()['tasksEditable']
            with pytest.raises(ValueError): notes.onenote.select(review, {'mode': 'local'}, OWN)
            with pytest.raises(ValueError): notes.patch(review.id, {'operationId': 'edit', 'tasks': {}})
        finally:
            release.set()
            await task
    asyncio.run(run())
    assert len(FakeGraph.created) == 1

def test_manual_choice_during_slow_default_lookup_is_preserved(setup, monkeypatch):
    notes, original = setup
    choose(notes, original); original.onenote['autoSave'] = False
    review = fresh(notes, original)
    started, release = threading.Event(), threading.Event()
    def sections(*args):
        started.set(); assert release.wait(5)
        return [{'id': 'section', 'name': 'Meetings', 'pagesUrl': PAGES}]
    monkeypatch.setattr(FakeGraph, 'sections', sections)
    async def run():
        notes.tick(OWN)
        try:
            assert await asyncio.to_thread(started.wait, 5)
            notes.onenote.select(review, {'mode': 'local'}, OWN)
        finally:
            release.set()
            await asyncio.gather(*list(notes.onenote.pending.values()))
        await tick(notes)
    asyncio.run(run())
    assert review.onenote['mode'] == 'local' and not FakeGraph.created

def test_preference_write_failure_rolls_back_without_auto_publication(setup, monkeypatch):
    notes, review = setup
    old = copy.deepcopy(review.onenote)
    def fail(): raise OSError('disk full')
    monkeypatch.setattr(notes.onenote, 'save_preferences', fail)
    with pytest.raises(ValueError, match='nicht gespeichert'): choose(notes, review)
    assert review.onenote == old and not notes.onenote.preferences['accounts']
    assert not FakeGraph.created and Path(review.document_path).exists()

def test_old_ready_review_is_not_automatically_uploaded_on_upgrade(setup):
    notes, review = setup
    choose(notes, review)
    review.onenote.pop('autoSave')
    notes.persist(review)
    restarted = Notes(notes.folder)
    asyncio.run(tick(restarted))
    assert restarted.reviews[review.id].onenote['mode'] == 'local'
    assert Path(restarted.reviews[review.id].document_path).exists()
    assert not FakeGraph.created


def test_crash_after_remote_success_before_local_success_keeps_md_and_reconciles(setup, monkeypatch):
    notes, review = setup
    document = Path(review.document_path)
    choose(notes, review)
    persist = notes.persist
    monkeypatch.setattr(notes, 'persist', lambda r: None if r.onenote.get('status') == 'saved' else persist(r))
    asyncio.run(tick(notes))
    assert document.exists() and len(FakeGraph.created) == 1
    restarted = Notes(notes.folder)
    assert restarted.reviews[review.id].onenote['status'] == 'uncertain'
    monkeypatch.setattr(FakeGraph, 'found', PAGE)
    asyncio.run(tick(restarted))
    assert restarted.reviews[review.id].onenote['status'] == 'saved'
    assert not document.exists() and len(FakeGraph.created) == 1


def test_changed_invitation_during_lookup_does_not_finalize_the_stale_target(setup, monkeypatch):
    notes, original = setup
    choose(notes, original); original.onenote['autoSave'] = False
    review = fresh(notes, original)
    started, release = threading.Event(), threading.Event()
    def sections(*args):
        started.set(); assert release.wait(5)
        return [{'id': 'section', 'name': 'Meetings', 'pagesUrl': PAGES}]
    monkeypatch.setattr(FakeGraph, 'sections', sections)
    async def run():
        notes.tick(OWN)
        try:
            assert await asyncio.to_thread(started.wait, 5)
            review.people = [dict(id='new', name='Other', email='p@other.example', source='calendar')]
        finally:
            release.set()
            await asyncio.gather(*list(notes.onenote.pending.values()))
        assert not review.onenote.get('finalized')
        await tick(notes)
    asyncio.run(run())
    assert review.onenote['mode'] == 'local' and not FakeGraph.created

def test_api_selection_itself_starts_move_after_meeting(setup, monkeypatch):
    import app
    notes, review = setup
    monkeypatch.setattr(app, 'NOTES', notes)
    # A test-owned loop keeps background delivery alive across requests.
    base = f'/api/notes/{review.id}/onenote'
    choose(notes, review)
    async def run():
        from httpx import ASGITransport, AsyncClient
        async with AsyncClient(transport=ASGITransport(app.api), base_url='http://localhost') as api:
            result = await api.post(base + '/select', json=dict(mode='onenote', account=ACCOUNT, book=BOOK, section='section'))
            assert result.json()['ok']
            await asyncio.gather(*list(notes.onenote.pending.values()))
    asyncio.run(run())
    assert len(FakeGraph.created) == 1 and review.onenote['status'] == 'saved'
