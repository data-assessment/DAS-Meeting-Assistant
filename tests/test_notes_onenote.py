import asyncio
import copy
import json
import uuid
from types import SimpleNamespace

import pytest
import requests
from engine import notes_onenote as no
from engine.meeting_notes import Notes, Review
from engine.notes_schema import Draft
from engine.speech.core import Transcript
REAL_GRAPH = no.Graph

BOOK = no.GRAPH + '/sites/team/onenote/notebooks/customer/sections'
PAGES = no.GRAPH + '/sites/team/onenote/sections/meetings/pages'
ACCOUNT = 'user:alex@own.example'
PAGE = {'id': 'page-123', 'links': {'oneNoteWebUrl': {'href': 'https://tenant.sharepoint.com/note'}}}
DRAFT = {'summary': 'Budget <script> vereinbart.', 'decisions': 'Pilot starten.', 'openQuestions': '',
         'tasks': [{'title': 'Angebot erstellen', 'owner': 'Anna', 'recipient': 'Contoso', 'due': '20.09.', 'uncertainty': ''},
                   {'title': 'Nicht übernehmen', 'owner': '', 'recipient': '', 'due': '', 'uncertainty': '', 'included': False}]}


class FakeGraph:
    response = 201
    fail = False
    found = None
    created = []
    account = ACCOUNT
    user = {'displayName': 'Alex', 'userPrincipalName': 'alex@own.example'}

    def __init__(self, *args): pass
    def create(self, target, content):
        self.created.append((target, content))
        if self.fail: raise requests.Timeout('Do not expose token or content')
        return SimpleNamespace(status_code=self.response, json=lambda: copy.deepcopy(PAGE))
    def find(self, target, marker, attempted_at=''): return self.found
    def notebooks(self): return [{'id': 'book', 'name': 'Contoso', 'label': 'Contoso (Team)', 'sectionsUrl': BOOK, 'webUrl': 'https://tenant.sharepoint.com/book'}], ''
    def sections(self, book): return [{'id': 'section', 'name': 'Besprechungen', 'pagesUrl': PAGES}]


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(no, 'Graph', FakeGraph)
    monkeypatch.setattr(FakeGraph, 'created', [])
    monkeypatch.setattr(FakeGraph, 'fail', False)
    monkeypatch.setattr(FakeGraph, 'response', 201)
    monkeypatch.setattr(FakeGraph, 'found', None)
    notes = Notes(tmp_path / 'notes')
    identity = str(uuid.uuid4())
    session = SimpleNamespace(id=identity, transcript=Transcript(identity, ''), stop=lambda: None)
    review = Review(session, 'Projektabstimmung', '2026-09-16T10:00:00', {})
    review.draft = Draft.model_validate(DRAFT).model_dump()
    review.ended = '2026-09-16T11:00:00'
    review.phase = 'complete'
    review.people = [{'id': 'p', 'name': 'Customer', 'email': 'person@contoso.com', 'source': 'calendar'}]
    notes.reviews[identity] = review
    notes.persist(review)
    return notes, review


def choose(notes, review, remember=True):
    async def discover():
        targets = await notes.onenote.targets(review, frozenset({'own.example'}))
        await notes.onenote.load_sections(ACCOUNT, BOOK)
        return targets
    targets = asyncio.run(discover())
    notes.onenote.select(review, {'mode': 'onenote', 'account': ACCOUNT, 'book': BOOK, 'section': 'section',
                                 'rememberDomain': targets['domain'] if remember else ''}, frozenset({'own.example'}))


def test_native_tasks_no_raw_or_unselected_content(setup):
    _, review = setup
    content = no.page_html(review, 'Alex & Co')
    assert 'data-tag="to-do"' in content and 'Anna' in content and '20.09.' in content
    assert 'Nicht übernehmen' not in content and '<script>' not in content
    assert '&lt;script&gt;' in content and 'Alex &amp; Co' in content
    assert 'meeting-' + review.id in content


def test_move_keeps_generated_md_until_confirmed_and_restart_does_not_recreate_it(setup):
    notes, review = setup
    assert list(notes.folder.glob('*.md'))
    choose(notes, review)
    assert list(notes.folder.glob('*.md'))
    restarted = Notes(notes.folder)
    assert restarted.reviews[review.id].onenote['mode'] == 'onenote'
    assert list(notes.folder.glob('*.md'))
    asyncio.run(restarted.onenote.publish(restarted.reviews[review.id], 0))
    assert not list(notes.folder.glob('*.md'))
    assert not list(Notes(notes.folder).folder.glob('*.md'))
    assert restarted.onenote.preferences['mode'] == 'local'
    assert restarted.onenote.preferences['accounts'][ACCOUNT]['domains']['contoso.com']['book'] == BOOK


def test_preserve_externally_edited_markdown(setup):
    notes, review = setup
    document = next(notes.folder.glob('*.md'))
    document.write_text('External edits', encoding='utf-8')
    choose(notes, review)
    asyncio.run(notes.onenote.publish(review, 0))
    assert document.read_text(encoding='utf-8') == 'External edits'
    assert 'veränderte' in review.onenote['localNotice']


def test_duplicate_click_and_restart_never_create_twice(setup):
    notes, review = setup
    choose(notes, review)
    async def twice():
        await asyncio.gather(notes.onenote.publish(review, 0), notes.onenote.publish(review, 0))
    asyncio.run(twice())
    restarted = Notes(notes.folder)
    asyncio.run(restarted.onenote.publish(restarted.reviews[review.id], 0))
    assert len(FakeGraph.created) == 1
    assert FakeGraph.created[0][0]['pagesUrl'] == PAGES
    assert not restarted.reviews[review.id].public()['editable']
    assert restarted.reviews[review.id].public()['tasksEditable']


def test_timeout_reconciles_without_second_post(setup, monkeypatch):
    notes, review = setup
    choose(notes, review)
    monkeypatch.setattr(FakeGraph, 'fail', True)
    with pytest.raises(ValueError, match='keine weitere Seite'):
        asyncio.run(notes.onenote.publish(review, 0))
    restarted = Notes(notes.folder)
    review = restarted.reviews[review.id]
    with pytest.raises(ValueError): asyncio.run(restarted.onenote.publish(review, 0))
    assert len(FakeGraph.created) == 1
    monkeypatch.setattr(FakeGraph, 'found', PAGE)
    asyncio.run(restarted.onenote.publish(review, 0))
    assert review.onenote['status'] == 'saved'
    assert len(FakeGraph.created) == 1


def test_crash_after_sending_marker_never_reposts(setup):
    notes, review = setup
    choose(notes, review)
    review.onenote['status'] = 'sending'
    notes.persist(review)
    restarted = Notes(notes.folder)
    with pytest.raises(ValueError): asyncio.run(restarted.onenote.publish(restarted.reviews[review.id], 0))
    assert not FakeGraph.created


def test_definite_rejection_can_be_retried(setup, monkeypatch):
    notes, review = setup
    choose(notes, review)
    monkeypatch.setattr(FakeGraph, 'response', 403)
    with pytest.raises(ValueError, match='403'): asyncio.run(notes.onenote.publish(review, 0))
    assert review.onenote['status'] == 'ready'
    monkeypatch.setattr(FakeGraph, 'response', 201)
    asyncio.run(notes.onenote.publish(review, 0))
    assert review.onenote['status'] == 'saved'


def test_no_network_write_without_durable_marker(setup, monkeypatch):
    notes, review = setup
    choose(notes, review)
    monkeypatch.setattr(notes, 'persist', lambda _: None)
    with pytest.raises(ValueError, match='lokaler Speicher'): asyncio.run(notes.onenote.publish(review, 0))
    assert not FakeGraph.created


def test_no_wrong_account_or_stale_revision_write(setup, monkeypatch):
    notes, review = setup
    choose(notes, review)
    with pytest.raises(ValueError, match='geändert'): asyncio.run(notes.onenote.publish(review, 99))
    monkeypatch.setattr(FakeGraph, 'account', 'other-account')
    with pytest.raises(ValueError, match='Microsoft-Konto'): asyncio.run(notes.onenote.publish(review, 0))
    assert not FakeGraph.created


def test_suggestions_ignore_unconfirmed_contacts_and_ambiguous_customers(setup):
    notes, review = setup
    result = asyncio.run(notes.onenote.targets(review, frozenset({'own.example'})))
    assert result['suggestedBook'] == BOOK and result['domain'] == 'contoso.com'
    review.people.append({'id': 'p2', 'name': 'Other', 'email': 'p@other.example', 'source': 'calendar'})
    result = asyncio.run(notes.onenote.targets(review, frozenset({'own.example'})))
    assert result['suggestedBook'] == '' and result['domain'] == ''
    review.people = [dict(p, source='contact') for p in review.people]
    result = asyncio.run(notes.onenote.targets(review, frozenset({'own.example'})))
    assert result['suggestedBook'] == '' and result['domain'] == ''


def test_learning_is_scoped_to_account(setup):
    notes, review = setup
    choose(notes, review)
    assert set(notes.onenote.preferences['accounts']) == {ACCOUNT}
    assert notes.onenote.preferences['accounts'][ACCOUNT]['sections'][BOOK] == 'section'


def test_destination_discovery_does_not_save_or_publish_and_done_remembers_choice(setup):
    notes, review = setup
    before = copy.deepcopy(notes.onenote.preferences)
    result = asyncio.run(notes.onenote.targets(review, frozenset({'own.example'})))
    asyncio.run(notes.onenote.load_sections(ACCOUNT, BOOK))
    assert not result['rememberDomain']
    assert notes.onenote.preferences == before and review.onenote['mode'] == 'local'
    choose(notes, review)
    result = asyncio.run(notes.onenote.targets(review, frozenset({'own.example'})))
    assert result['rememberDomain']
    assert review.onenote['status'] == 'ready' and not FakeGraph.created


def test_unchecking_customer_preference_is_persisted_and_scoped(setup):
    notes, review = setup
    choose(notes, review)
    mapping = notes.onenote.preferences['accounts'][ACCOUNT]['domains']
    mapping['unrelated.example'] = 'other-book'
    notes.onenote.select(review, {'mode': 'onenote', 'account': ACCOUNT, 'book': BOOK, 'section': 'section',
                                'forgetDomain': 'unrelated.example'}, frozenset({'own.example'}))
    assert mapping['unrelated.example'] == 'other-book'
    notes.onenote.select(review, {'mode': 'onenote', 'account': ACCOUNT, 'book': BOOK, 'section': 'section',
                                'forgetDomain': 'contoso.com'}, frozenset({'own.example'}))
    restarted = Notes(notes.folder)
    assert 'contoso.com' not in restarted.onenote.preferences['accounts'][ACCOUNT]['domains']
    assert not FakeGraph.created


def test_selected_target_must_come_from_catalog(setup):
    notes, review = setup
    with pytest.raises(ValueError):
        notes.onenote.select(review, {'mode': 'onenote', 'account': ACCOUNT, 'book': 'evil', 'section': 'x'}, frozenset())
    assert review.onenote['mode'] == 'local'


@pytest.mark.parametrize('url', ['https://graph.microsoft.com.evil/v1.0/me', 'http://graph.microsoft.com/v1.0/me',
                                'https://graph.microsoft.com@evil/v1.0/me', 'https://evil/v1.0/me'])
def test_graph_token_cannot_be_forwarded_to_foreign_host(url):
    with pytest.raises(ValueError): no.graph_url(url)


def test_saved_summary_and_destination_stay_locked_but_tasks_are_editable(setup):
    notes, review = setup
    choose(notes, review)
    asyncio.run(notes.onenote.publish(review, 0))
    with pytest.raises(ValueError): notes.patch(review.id, {'operationId': 'x', 'text': {'summary': 'Changed', 'decisions': '', 'openQuestions': ''}})
    notes.patch(review.id, {'operationId': 'y', 'tasks': {}})
    with pytest.raises(ValueError): notes.onenote.select(review, {'mode': 'local'}, frozenset())
    assert review.onenote['url'] == PAGE['links']['oneNoteWebUrl']['href']


def test_separate_clients_intentionally_create_separate_pages(setup):
    notes, review = setup
    choose(notes, review)
    other = copy.copy(review)
    other.session = SimpleNamespace(id=str(uuid.uuid4()), transcript=Transcript('', ''))
    other.onenote = copy.deepcopy(review.onenote)
    notes.reviews[other.id] = other
    asyncio.run(notes.onenote.publish(review, 0))
    asyncio.run(notes.onenote.publish(other, 0))
    assert len(FakeGraph.created) == 2
    assert 'meeting-' + review.id in FakeGraph.created[0][1]
    assert 'meeting-' + other.id in FakeGraph.created[1][1]


def test_collection_follows_pagination_and_rejects_foreign_next_link(monkeypatch):
    graph = object.__new__(REAL_GRAPH)
    graph.headers = {'Authorization': 'Bearer test'}
    replies = [{'value': [{'id': 'a'}], '@odata.nextLink': no.GRAPH + '/next'}, {'value': [{'id': 'b'}]}]
    calls = []
    def get(url, **kwargs):
        calls.append(url)
        return SimpleNamespace(status_code=200, json=lambda: replies.pop(0))
    monkeypatch.setattr(no.requests, 'get', get)
    assert [v['id'] for v in graph.collection(no.GRAPH + '/start')] == ['a', 'b']
    replies.append({'value': [], '@odata.nextLink': 'https://evil.example/v1.0/next'})
    with pytest.raises(ValueError): graph.collection(no.GRAPH + '/start')
    assert 'https://evil.example/v1.0/next' not in calls


def test_nested_sections_keep_shared_site_pages_endpoint(monkeypatch):
    graph = object.__new__(REAL_GRAPH)
    group = no.GRAPH + '/sites/team/onenote/sectionGroups/project'
    data = {BOOK: [], BOOK.rsplit('/sections', 1)[0] + '/sectionGroups': [{'displayName': 'Projekt', 'sectionsUrl': group + '/sections'}],
            group + '/sections': [{'id': 's', 'displayName': 'Meetings', 'pagesUrl': PAGES}], group + '/sectionGroups': []}
    monkeypatch.setattr(graph, 'collection', lambda url: data[url])
    assert graph.sections({'sectionsUrl': BOOK}) == [{'id': 's', 'name': 'Projekt › Meetings', 'pagesUrl': PAGES}]


def test_reconcile_matches_only_own_marker(monkeypatch):
    graph = object.__new__(REAL_GRAPH)
    pages = [{'id': 'other', 'contentUrl': no.GRAPH + '/other'}, {'id': 'ours', 'contentUrl': no.GRAPH + '/ours'}]
    monkeypatch.setattr(graph, 'collection', lambda url: pages)
    monkeypatch.setattr(graph, 'get', lambda url: SimpleNamespace(text='<p data-id="meeting-' + ('one' if url.endswith('/ours') else 'two') + '">Date</p>'))
    assert graph.find({'pagesUrl': PAGES}, 'meeting-one')['id'] == 'ours'


def test_api_exports_derived_notes_and_rejects_cross_origin(setup, monkeypatch):
    import app
    from fastapi.testclient import TestClient
    notes, review = setup
    monkeypatch.setattr(app, 'NOTES', notes)
    client = TestClient(app.api)
    base = f'/api/notes/{review.id}/onenote'
    assert client.post(base + '/targets', json={}).json()['ok']
    assert client.post('/api/notes/onenote/sections', json={'account': ACCOUNT, 'book': BOOK}).json()['ok']
    assert client.post(base + '/select', json={'mode': 'onenote', 'account': ACCOUNT, 'book': BOOK, 'section': 'section'}, headers={'origin': 'https://evil.example'}).status_code == 403
    assert client.post(base + '/publish', json={'revision': 0}, headers={'origin': 'https://evil.example'}).status_code == 403
    assert not FakeGraph.created
    choose(notes, review)
    assert client.post(base + '/publish', json={'revision': 0}).json()['ok']
    assert client.get('/api/notes').json()['reviews'][0]['onenote']['status'] == 'saved'


def test_new_meetings_start_local_until_a_customer_rule_matches(setup, monkeypatch):
    from test_meeting_notes import DEVICES, FakeSession
    notes, review = setup
    choose(notes, review)
    notes.configure({'enabled': True, 'speechKey': 'placeholder', 'chatKey': 'placeholder', 'endpoint': 'https://example.openai.azure.com', 'model': 'test'})
    fresh = notes.start('Nächster Termin', '2026-09-16T12:00:00', DEVICES, FakeSession)
    fresh.draft = Draft.model_validate(DRAFT).model_dump()
    notes.persist(fresh)
    assert fresh.onenote['mode'] == 'local'
    assert fresh.onenote['autoSave']
    assert list(notes.folder.glob('*.md'))
    notes.shutdown()


def test_group_notebooks_with_limited_membership_properties_and_duplicate(monkeypatch):
    import config
    monkeypatch.setattr(config, 'ONENOTE_SITE_PATHS', [])
    graph = object.__new__(REAL_GRAPH)
    graph.headers = {}
    personal = no.GRAPH + '/me/onenote/notebooks/shared/sections'
    replies = {
        no.GRAPH + '/me/onenote/notebooks?includeSharedNotebooks=true': {'value': [dict(id='shared', displayName='Shared', sectionsUrl=personal)]},
        no.GRAPH + '/me/memberOf': {'value': [{'@odata.type': '#microsoft.graph.group', 'id': 'g1', 'displayName': None, 'groupTypes': []}], '@odata.nextLink': no.GRAPH + '/membership-next'},
        no.GRAPH + '/membership-next': {'value': [{'@odata.type': '#microsoft.graph.group', 'id': 'g2', 'displayName': 'Vertrieb', 'groupTypes': ['Unified']},
                                                {'@odata.type': '#microsoft.graph.directoryRole', 'id': 'role'}]},
        no.GRAPH + '/groups/g1/onenote/notebooks': {'value': [dict(id='shared', displayName='Shared', sectionsUrl=no.GRAPH + '/groups/g1/onenote/notebooks/shared/sections')], '@odata.nextLink': no.GRAPH + '/books-next'},
        no.GRAPH + '/books-next': {'value': [dict(id='projects', displayName='Projects', sectionsUrl=no.GRAPH + '/groups/g1/onenote/notebooks/projects/sections')]},
        no.GRAPH + '/groups/g2/onenote/notebooks': {'value': [dict(id='v3', displayName='V3', sectionsUrl=no.GRAPH + '/groups/g2/onenote/notebooks/v3/sections')]},
    }
    monkeypatch.setattr(no.requests, 'get', lambda url, **kw: SimpleNamespace(status_code=200, json=lambda: replies[url]))
    books, warning = graph.notebooks()
    assert not warning
    assert [b['name'] for b in books] == ['Projects', 'Shared', 'V3']
    assert books[1]['sectionsUrl'] == personal
    assert books[2]['label'] == 'V3 · Vertrieb'


@pytest.mark.parametrize('status', [404, 403, 429, 503])
def test_unavailable_group_keeps_accessible_notebooks(monkeypatch, status):
    import config
    monkeypatch.setattr(config, 'ONENOTE_SITE_PATHS', [])
    graph = object.__new__(REAL_GRAPH)
    def collection(url):
        if '/me/memberOf' in url:
            return [{'@odata.type': '#microsoft.graph.group', 'id': 'missing'}, {'@odata.type': '#microsoft.graph.group', 'id': 'accessible'}]
        if '/groups/missing/' in url: raise no.GraphReadError(status)
        if '/groups/accessible/' in url: return [dict(id='a', displayName='Projects', sectionsUrl=BOOK)]
        return []
    monkeypatch.setattr(graph, 'collection', collection)
    books, warning = graph.notebooks()
    assert [b['name'] for b in books] == ['Projects']
    assert bool(warning) == (status != 404)


def test_personal_failure_does_not_hide_group_books(monkeypatch):
    import config
    monkeypatch.setattr(config, 'ONENOTE_SITE_PATHS', [])
    graph = object.__new__(REAL_GRAPH)
    def collection(url):
        if '/me/onenote/' in url: raise no.GraphReadError(403)
        if '/me/memberOf' in url: return [{'@odata.type': '#microsoft.graph.group', 'id': 'g'}]
        return [dict(id='g', displayName='V3', sectionsUrl=BOOK)]
    monkeypatch.setattr(graph, 'collection', collection)
    books, warning = graph.notebooks()
    assert books[0]['name'] == 'V3'
    assert 'Persönliche' in warning
