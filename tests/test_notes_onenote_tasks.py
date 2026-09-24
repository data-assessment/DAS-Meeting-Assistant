import asyncio
import copy
import json
import threading
import uuid
import xml.etree.ElementTree as ET
from types import SimpleNamespace

import pytest
import requests
from engine import notes_onenote as no, notes_onenote_tasks as ts
from engine.meeting_notes import Notes
from test_notes_onenote import setup, choose, FakeGraph, ACCOUNT, REAL_GRAPH


class Remote:
    """Graph-shaped HTML with IDs that change after every mutation."""
    def __init__(self, content):
        self.root = ET.fromstring(content)
        self.writes, self.reads, self.generation = [], [], 0
        self.fail_read = self.fail_write = self.apply_then_fail = False
        self.response = 204
        self.on_write = lambda: None
        self.ids()

    def ids(self):
        self.generation += 1
        for i, element in enumerate(self.root.iter()):
            element.set('id', f'gen{self.generation}-{i}')

    @property
    def text(self):
        return ET.tostring(self.root, encoding='unicode')

    def get(self, url):
        self.reads.append(url)
        if self.fail_read:
            raise requests.ConnectionError('private token must never appear')
        return SimpleNamespace(text=self.text)

    def update(self, url, commands):
        self.writes.append((url, copy.deepcopy(commands)))
        self.on_write()
        if self.fail_write:
            raise requests.Timeout('private token must never appear')
        if self.response == 204:
            for command in commands:
                found = [el for el in self.root.iter() if el.get('id') == command['target']]
                assert len(found) == 1, 'Graph-generated target ID must still exist'
                node = found[0]
                parent = next(el for el in self.root.iter() if node in list(el))
                position = list(parent).index(node)
                fragment = list(ET.fromstring('<root>' + command['content'] + '</root>'))
                if command['action'] == 'replace':
                    parent.remove(node)
                else:
                    assert command['action'] == 'insert' and command['position'] == 'after'
                    position += 1
                for new in reversed(fragment):
                    parent.insert(position, new)
            self.ids()
        if self.apply_then_fail:
            raise requests.Timeout('private token must never appear')
        return SimpleNamespace(status_code=self.response)

    def main(self):
        return next(el for el in self.root.iter('p') if 'to-do' in el.get('data-tag', ''))


@pytest.fixture
def published(setup, monkeypatch):
    notes, review = setup
    choose(notes, review)
    asyncio.run(notes.onenote.publish(review, review.revision))
    remote = Remote(FakeGraph.created[0][1])
    monkeypatch.setattr(FakeGraph, 'get', lambda self, url: remote.get(url), raising=False)
    monkeypatch.setattr(FakeGraph, 'update_tasks', lambda self, url, commands: remote.update(url, commands), raising=False)
    return notes, review, remote


def edit(notes, review, **fields):
    task = review.draft['tasks'][0]
    return notes.patch(review.id, {'operationId': str(uuid.uuid4()), 'tasks': {task['id']: fields}})


def sync(notes, review):
    asyncio.run(notes.onenote.sync_tasks(review))


def test_owner_after_publication_saves_same_page_automatically_and_survives_restart(published):
    notes, review, remote = published
    result = edit(notes, review, owner='Customer', ownerId='p')
    assert result['draft']['tasks'][0]['ownerId'] == 'p'
    assert review.public()['tasksEditable'] and not review.public()['editable']
    assert review.onenote['taskSync']['status'] == 'pending'
    async def run():
        notes.onenote.schedule(review, frozenset())
        notes.onenote.schedule(review, frozenset())
        await asyncio.gather(*notes.jobs)
    asyncio.run(run())
    assert len(remote.writes) == 1 and len(FakeGraph.created) == 1
    assert remote.writes[0][0] == no.GRAPH + '/sites/team/onenote/pages/page-123/content'
    assert remote.reads[0].endswith('?includeIDs=true')
    assert 'Customer' in remote.main().text
    assert review.onenote['taskSync']['status'] == 'saved'
    restored = Notes(notes.folder).reviews[review.id]
    assert restored.public()['tasksEditable']
    assert restored.draft['tasks'][0]['ownerId'] == 'p'
    assert not list(notes.folder.glob('*.md'))


def test_legacy_page_and_missing_baseline_are_migrated_by_exact_paragraph(published):
    notes, review, remote = published
    review.onenote.pop('taskBase')
    review.onenote.pop('taskSync')
    for node in remote.root.iter('p'):
        if not node.get('data-id', '').startswith('meeting-'):
            node.attrib.pop('data-id', None)
    edit(notes, review, owner='Beate Muster', recipient='Updated recipient', due='Montag')
    sync(notes, review)
    assert review.onenote['taskSync']['status'] == 'saved'
    assert 'Beate Muster' in remote.main().text and 'Montag' in remote.main().text
    assert 'Updated recipient' in remote.text and 'Empfänger: Contoso' not in remote.text
    assert remote.main().get('data-id').startswith('task-')
    assert len(FakeGraph.created) == 1


def test_checked_state_other_tasks_summary_and_annotations_are_preserved(published):
    notes, review, remote = published
    remote.main().set('data-tag', 'important,to-do:completed')
    parent = remote.root.find('body')
    ET.SubElement(parent, 'p', {'id': 'external'}).text = 'Annotation by colleague'
    summary = next(el for el in parent if el.tag == 'p' and 'Budget' in ''.join(el.itertext()))
    summary.text = 'Summary edited directly in OneNote'
    before = remote.text
    edit(notes, review, owner='Beate Muster')
    sync(notes, review)
    assert len(remote.writes[0][1]) == 1
    assert remote.main().get('data-tag') == 'important,to-do:completed'
    assert 'Annotation by colleague' in remote.text and summary.text in remote.text
    assert 'Beate Muster' not in before and 'Beate Muster' in remote.text


@pytest.mark.parametrize('change', ['text', 'attachment', 'link', 'duplicate', 'marker'])
def test_external_changes_fail_closed_and_local_correction_survives(published, change):
    notes, review, remote = published
    node = remote.main()
    if change == 'text': node.text += ' (colleague changed this)'
    elif change == 'attachment': ET.SubElement(node, 'img', {'src': 'resource'})
    elif change == 'link': ET.SubElement(node, 'a', {'href': 'https://example.org'})
    elif change == 'duplicate': remote.root.find('body').append(copy.deepcopy(node))
    else:
        next(el for el in remote.root.iter('p') if el.get('data-id', '').startswith('meeting-')).set('data-id', 'another-meeting')
    before = remote.text
    edit(notes, review, owner='Beate Muster')
    sync(notes, review)
    assert not remote.writes and remote.text == before
    assert review.onenote['taskSync']['status'] == 'conflict'
    assert Notes(notes.folder).reviews[review.id].draft['tasks'][0]['owner'] == 'Beate Muster'
    assert review.public()['tasksEditable']


def test_remote_owner_change_does_not_block_independent_recipient_edit(published):
    notes, review, remote = published
    remote.main().text += ' (direct correction)'
    edit(notes, review, recipient='Updated recipient')
    sync(notes, review)
    assert review.onenote['taskSync']['status'] == 'saved'
    assert '(direct correction)' in remote.main().text and 'Updated recipient' in remote.text


def test_task_deselection_and_reselection_have_no_duplicate_checkboxes(published):
    notes, review, remote = published
    edit(notes, review, included=False)
    sync(notes, review)
    assert 'Keine Aufgaben ausgewählt.' in remote.text
    assert not any('to-do' in p.get('data-tag', '') for p in remote.root.iter('p'))
    edit(notes, review, included=True, title='Changed title', recipient='New recipient')
    sync(notes, review)
    assert review.onenote['taskSync']['status'] == 'saved'
    assert sum('to-do' in p.get('data-tag', '') for p in remote.root.iter('p')) == 1
    assert 'Changed title' in remote.main().text and 'Keine Aufgaben ausgewählt.' not in remote.text
    assert len(FakeGraph.created) == 1


def test_inserting_optional_fields_places_them_after_the_task(published):
    notes, review, remote = published
    edit(notes, review, recipient='')
    sync(notes, review)
    # Emulate OneNote dropping empty paragraphs after replacement.
    body = remote.root.find('body')
    for node in list(body):
        if node.tag == 'p' and not node.text: body.remove(node)
    edit(notes, review, owner='Beate', recipient='New recipient', questions=[{
        'id': 'question', 'field': 'context', 'label': 'Format?', 'options': [], 'answer': 'PDF'}])
    sync(notes, review)
    assert review.onenote['taskSync']['status'] == 'saved'
    text = [''.join(el.itertext()) for el in body]
    index = next(i for i, line in enumerate(text) if 'Beate' in line)
    assert text[index + 1:index + 3] == ['Empfänger: New recipient', 'Format? PDF']


def test_read_failure_keeps_edits_and_can_be_retried(published):
    notes, review, remote = published
    remote.fail_read = True
    edit(notes, review, owner='Beate')
    sync(notes, review)
    assert review.onenote['taskSync']['status'] == 'error' and not remote.writes
    assert 'private token' not in review.onenote['taskSync']['error']
    remote.fail_read = False
    sync(notes, review)
    assert review.onenote['taskSync']['status'] == 'saved'


@pytest.mark.parametrize('status', [401, 403, 404, 429])
def test_definite_rejection_can_retry_without_duplicate_page(published, status):
    notes, review, remote = published
    remote.response = status
    edit(notes, review, owner='Beate')
    sync(notes, review)
    assert review.onenote['taskSync']['status'] == 'error'
    remote.response = 204
    sync(notes, review)
    assert review.onenote['taskSync']['status'] == 'saved' and len(FakeGraph.created) == 1


@pytest.mark.parametrize('applied', [True, False])
def test_timeout_restart_and_status_check_never_repeat_patch(published, applied):
    notes, review, remote = published
    remote.apply_then_fail, remote.fail_write = applied, not applied
    edit(notes, review, owner='Beate')
    sync(notes, review)
    assert review.onenote['taskSync']['status'] == 'uncertain'
    restarted = Notes(notes.folder)
    review = restarted.reviews[review.id]
    sync(restarted, review)
    assert review.onenote['taskSync']['status'] == ('saved' if applied else 'uncertain')
    sync(restarted, review)
    assert len(remote.writes) == 1 and len(FakeGraph.created) == 1


def test_more_edits_while_sending_remain_pending_until_next_update(published):
    notes, review, remote = published
    entered, release = threading.Event(), threading.Event()
    def delay():
        entered.set()
        assert release.wait(5)
    remote.on_write = delay
    edit(notes, review, owner='Beate')
    async def run():
        job = asyncio.create_task(notes.onenote.sync_tasks(review))
        assert await asyncio.to_thread(entered.wait, 5)
        record = json.loads(next(notes.state_folder.glob('Meeting-Notizen-*.json')).read_text(encoding='utf-8'))
        assert record['onenote']['taskSync']['status'] == 'sending'
        edit(notes, review, owner='Beate Muster')
        with pytest.raises(ValueError): notes.discard(review.id)
        release.set()
        await job
    asyncio.run(run())
    assert review.onenote['taskSync']['status'] == 'pending' and 'Beate Muster' not in remote.main().text
    remote.on_write = lambda: None
    sync(notes, review)
    assert review.onenote['taskSync']['status'] == 'saved' and 'Beate Muster' in remote.main().text


def test_crash_sending_record_restores_as_uncertain(published):
    notes, review, remote = published
    edit(notes, review, owner='Beate')
    review.onenote['taskSync'] = {'status': 'sending', 'attempt': ts.snapshot(review.draft)}
    notes.persist(review)
    restarted = Notes(notes.folder)
    restored = restarted.reviews[review.id]
    assert restored.onenote['taskSync']['status'] == 'uncertain'
    sync(restarted, restored)
    assert restored.onenote['taskSync']['status'] == 'uncertain' and not remote.writes


def test_partial_write_cannot_be_repeated(published):
    notes, review, remote = published
    edit(notes, review, owner='Beate', recipient='Changed')
    desired = ts.snapshot(review.draft)
    commands = ts.plan(remote.text, review.id, review.onenote['taskBase'], desired)
    remote.update('url', commands[:1])
    review.onenote['taskSync'] = {'status': 'uncertain', 'attempt': desired}
    sync(notes, review)
    assert review.onenote['taskSync']['status'] == 'uncertain' and len(remote.writes) == 1


def test_wrong_account_and_failed_durable_write_prevent_patch(published, monkeypatch):
    notes, review, remote = published
    edit(notes, review, owner='Beate')
    monkeypatch.setattr(FakeGraph, 'account', 'other-account')
    sync(notes, review)
    assert not remote.writes and not remote.reads
    monkeypatch.setattr(FakeGraph, 'account', ACCOUNT)
    monkeypatch.setattr(notes, 'persist', lambda _: False)
    sync(notes, review)
    assert not remote.writes and review.onenote['taskSync']['status'] == 'pending'


def test_tasks_sync_api_and_origin_guard(published, monkeypatch):
    import app
    from fastapi.testclient import TestClient
    notes, review, remote = published
    monkeypatch.setattr(app, 'NOTES', notes)
    client = TestClient(app.api)
    base = f'/api/notes/{review.id}'
    task_id = review.draft['tasks'][0]['id']
    assert client.post(base + '/edit', json={'operationId': 'api', 'tasks': {task_id: {'owner': 'Beate'}}}).json()['ok']
    assert client.post(base + '/onenote/tasks-sync', headers={'origin': 'https://evil.example'}).status_code == 403
    assert not remote.writes
    result = client.post(base + '/onenote/tasks-sync', json={}).json()
    assert result['ok'], result
    assert client.get('/api/notes').json()['reviews'][0]['onenote']['taskSync']['status'] == 'saved'


def test_graph_patch_validates_host_and_does_not_follow_redirects(monkeypatch):
    graph = object.__new__(REAL_GRAPH)
    graph.headers = {'Authorization': 'Bearer test'}
    calls = []
    monkeypatch.setattr(no.requests, 'patch', lambda url, **kwargs: calls.append((url, kwargs)))
    with pytest.raises(ValueError): graph.update_tasks('https://evil.example/content', [])
    assert not calls
    graph.update_tasks(no.GRAPH + '/groups/g/onenote/pages/p/content', [{'target': 'generated', 'action': 'replace', 'content': '<p>new</p>'}])
    assert calls[0][1]['allow_redirects'] is False and calls[0][1]['timeout'] == 45


def test_pending_edit_resumes_after_restart(published):
    notes, review, remote = published
    edit(notes, review, owner='Beate')
    restarted = Notes(notes.folder)
    restored = restarted.reviews[review.id]
    async def run():
        restarted.onenote.schedule(restored, frozenset())
        await asyncio.gather(*restarted.jobs)
    asyncio.run(run())
    assert restored.onenote['taskSync']['status'] == 'saved'
    assert len(remote.writes) == 1 and 'Beate' in remote.main().text


def test_uncertain_attempt_keeps_later_edit_until_confirmed(published):
    notes, review, remote = published
    remote.apply_then_fail = True
    edit(notes, review, owner='Beate')
    sync(notes, review)
    edit(notes, review, owner='Beate Muster')
    assert review.onenote['taskSync']['status'] == 'uncertain'
    sync(notes, review)
    assert review.onenote['taskSync']['status'] == 'pending'
    remote.apply_then_fail = False
    sync(notes, review)
    assert review.onenote['taskSync']['status'] == 'saved'
    assert 'Beate Muster' in remote.main().text and len(remote.writes) == 2


def test_legacy_baseline_precedes_owner_reconciliation_on_reload(published, monkeypatch):
    from engine import notes_people
    notes, review, remote = published
    path = next(notes.state_folder.glob('Meeting-Notizen-*.json'))
    data = json.loads(path.read_text(encoding='utf-8'))
    old_base = data['onenote'].pop('taskBase')
    data['onenote'].pop('taskSync')
    path.write_text(json.dumps(data), encoding='utf-8')
    def reconcile(loaded):
        loaded.draft['tasks'][0]['owner'] = 'Anna Kalendername'
        return True
    monkeypatch.setattr(notes_people, 'reconcile_owners', reconcile)
    restored = Notes(notes.folder).reviews[review.id]
    assert restored.draft['tasks'][0]['owner'] == 'Anna Kalendername'
    assert restored.onenote['taskBase'] == old_base
    assert not remote.writes


def test_legacy_changed_task_cannot_be_silently_deselected(published):
    notes, review, remote = published
    remote.main().attrib.pop('data-id')
    remote.main().text += ' (changed in OneNote)'
    edit(notes, review, included=False)
    sync(notes, review)
    assert review.onenote['taskSync']['status'] == 'conflict' and not remote.writes
