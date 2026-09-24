import asyncio
import copy
import datetime as dt
import json
from pathlib import Path

import pytest
import app
from engine import notes_people as people, meeting_notes as mn
from engine.notes_schema import Draft
from engine.notes_markdown import render
from engine.notes_onenote import page_html
from test_meeting_notes import notes, start, finish, DRAFT


def invitation(review):
    review.calendar_selected = 'event'
    review.calendar_candidates = [{'id': 'event', 'title': 'Transcriber Test',
        'start': dt.datetime(2026, 9, 17, 15, 50).astimezone().isoformat(), 'end': '', 'organizer': '',
        'people': [people.person('Moritz Muster', 'moritz@example.org', 'calendar'), people.person('Theo Beispiel', 'theo@example.org', 'calendar')]}]
    review.people = copy.deepcopy(review.calendar_candidates[0]['people'])


def tasks(review, owners):
    draft = copy.deepcopy(DRAFT)
    draft['tasks'] = [dict(draft['tasks'][0], id=f'task{i}', owner=name, ownerId='') for i, name in enumerate(owners)]
    review.draft = Draft.model_validate(draft).model_dump()


def test_all_headings_use_invitation_title_and_scheduled_time(notes):
    r = start(notes); finish(notes, r)
    invitation(r)
    r.title = 'Teams-Meeting'
    expected = '17.09.2026 · 15:50 · Transcriber Test'
    assert r.public()['displayTitle'] == expected
    assert render(r).startswith('# ' + expected + '\n')
    assert '<title>' + expected + '</title>' in page_html(r, 'Author')


def test_stopping_capture_does_not_replace_outlook_title(notes, monkeypatch):
    r = start(notes)
    invitation(r)
    notes.set_meeting_title(r, 'Transcriber Test')
    monkeypatch.setattr(app, 'STATE', app.AppState())
    app.STATE.active, app.STATE.title = True, 'Teams-Meeting'
    monkeypatch.setattr(app, '_refresh_tray', lambda: None)
    async def quiet(*_): pass
    monkeypatch.setattr(app, 'broadcast', quiet)
    async def run():
        done = asyncio.Event()
        async def finalize(review):
            await notes.finish(review)
            done.set()
        monkeypatch.setattr(app, '_finish_notes', finalize)
        await app._end_meeting()
        await asyncio.wait_for(done.wait(), 3)
    asyncio.run(run())
    assert r.title == 'Transcriber Test'
    assert json.loads(Path(r.saved_path).read_text(encoding='utf-8'))['title'] == 'Transcriber Test'


def test_first_names_link_to_invited_people_and_survive_restart(notes):
    r = start(notes); finish(notes, r)
    invitation(r); tasks(r, ['Moritz', 'Theo'])
    notes.persist(r)
    assert [(t['owner'], t['ownerId']) for t in r.draft['tasks']] == [(p['name'], p['id']) for p in r.people]
    revision = r.revision
    notes.persist(r)
    assert r.revision == revision
    restored = mn.Notes(notes.folder).reviews[r.id]
    assert restored.draft == r.draft
    assert 'Moritz Muster' in render(restored) and 'Theo Beispiel' in page_html(restored, 'Author')


def test_late_invitation_resolves_existing_tasks(notes):
    r = start(notes); finish(notes, r)
    tasks(r, ['Moritz', 'Theo'])
    notes.persist(r)
    assert all(not t['ownerId'] for t in r.draft['tasks'])
    invitation(r)
    r.calendar_selected = ''
    people.select_calendar(notes, r, 'event')
    assert [t['owner'] for t in r.draft['tasks']] == ['Moritz Muster', 'Theo Beispiel']


def test_ambiguous_name_and_unconfirmed_contacts_do_not_link(notes):
    r = start(notes); invitation(r); tasks(r, ['Moritz', 'Theo', 'ich'])
    r.people.append(people.person('Moritz Müller', 'max2@example.org', 'calendar'))
    r.people[1]['source'] = 'contact'
    notes.persist(r)
    assert all(not t['ownerId'] for t in r.draft['tasks'])
    r.draft['tasks'][0]['owner'] = 'Moritz Muster'
    notes.persist(r)
    assert r.draft['tasks'][0]['ownerId'] == r.people[0]['id']


def test_manual_assignment_or_explicit_owner_edit_is_preserved(notes):
    r = start(notes); invitation(r); tasks(r, ['Moritz', 'Theo'])
    r.draft['tasks'][0].update(owner='Andere Person', ownerId='manual-person')
    r.task_edits['task1'] = {'owner': 'Theo', 'ownerId': ''}
    notes.persist(r)
    assert r.draft['tasks'][0]['ownerId'] == 'manual-person'
    assert r.draft['tasks'][1]['owner'] == 'Theo' and not r.draft['tasks'][1]['ownerId']


@pytest.mark.parametrize('status', ['sending', 'uncertain', 'saved'])
def test_onenote_submission_snapshot_is_not_reassigned(notes, status):
    r = start(notes); invitation(r); tasks(r, ['Moritz'])
    r.onenote.update(mode='onenote', status=status)
    notes.persist(r)
    assert r.draft['tasks'][0]['owner'] == 'Moritz' and not r.draft['tasks'][0]['ownerId']


def test_unlinked_saved_draft_is_resolved_on_loading(notes):
    r = start(notes); finish(notes, r)
    invitation(r); tasks(r, ['Moritz'])
    notes.persist(r)
    path = Path(r.saved_path)
    data = json.loads(path.read_text(encoding='utf-8'))
    data['tasks'][0].update(owner='Moritz', ownerId='')
    path.write_text(json.dumps(data), encoding='utf-8')
    restored = mn.Notes(notes.folder).reviews[r.id]
    assert restored.draft['tasks'][0]['owner'] == 'Moritz Muster'
    assert restored.draft['tasks'][0]['ownerId'] == r.people[0]['id']


@pytest.mark.parametrize('named_owner', ['Beate', ''])
def test_bettina_links_only_when_an_owner_was_extracted(notes, named_owner):
    r = start(notes); finish(notes, r)
    r.people = [people.person('Beate Muster', 'beate@example.org', 'calendar')]
    tasks(r, [named_owner])
    notes.persist(r)
    task = r.draft['tasks'][0]
    assert task['owner'] == ('Beate Muster' if named_owner else '')
    assert task['ownerId'] == (r.people[0]['id'] if named_owner else '')
