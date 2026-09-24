# OneNote für Meeting-Notizen (0.40.6)

## Voreinstellung

Ohne gemerkte Kundenzuordnung ist **Auf diesem PC** vorausgewählt. Eine passende
E-Mail-Domäne kann ein Notizbuch vorschlagen; ein Vorschlag allein löst keine
automatische Übertragung aus. **Dieses Ziel für Termine mit … merken** speichert
Notizbuch und Abschnitt für die Kundendomäne und das angemeldete Microsoft-Konto.
Weitere Termine mit Teilnehmern dieser Domäne werden dort automatisch abgelegt.
Eigene Domänen, öffentliche Mailanbieter und unbestätigte Kontaktvorschläge zählen
nicht als Kundensignal. Die zuletzt gewählte Ablageart ist kein globaler Standard.

Mehrere bekannte Domänen mit demselben Ziel sind eindeutig. Bei widersprüchlichen
Zielen bleibt dieser PC vorausgewählt, mit einem Hinweis und der Möglichkeit,
ein Ziel zu wählen. Ein nicht erreichbares oder gelöschtes Kundenziel führt ebenfalls
zur lokalen Ablage mit Hinweis; die gespeicherte Zuordnung bleibt erhalten.

## Während des Meetings

Die Meeting-Ansicht enthält die Auswahl **Auf diesem PC / OneNote**. OneNote öffnet
den Dialog **OneNote-Ziel wählen**. Notizbuch und Abschnitt sind durchsuchbare
Auswahlfelder. **Fertig** übernimmt das Ziel. **Abbrechen**, Escape und × lassen
die bisherige Wahl unverändert. Ohne gesetztes Merken-Häkchen gilt die Änderung
nur für dieses Meeting. PC für dieses Meeting zu wählen löscht keine Kundenzuordnung.

Nach Meeting-Ende und fertiger Zusammenfassung erfolgt die Ablage automatisch.
Es gibt keinen zusätzlichen Speichern-Klick. „Meeting-Fenster schließen“ blendet das Fenster aus;
Verarbeitung und Ablage laufen weiter. Erst Quit beendet den Client.

## Später nach OneNote verschieben

Ein abgeschlossenes lokales Meeting zeigt **Auf diesem PC gespeichert** und
**Nach OneNote verschieben …**. Diese Aktion ist auch nach dem Öffnen eines
früheren Meetings verfügbar. Im Dialog das Ziel wählen und **Verschieben** klicken:
Die aktuelle Zusammenfassung und ausgewählten Aufgaben werden sofort übertragen.

Die lokale MD-Datei bleibt bis zur bestätigten OneNote-Ablage erhalten. Danach
wird nur die unveränderte, vom Client erzeugte Datei entfernt. Extern geänderte
Dateien bleiben mit einem Hinweis erhalten. Der interne Wiederherstellungsstand
in `.app-state` bleibt bestehen. Bei direkt gewähltem OneNote entsteht keine
zusätzliche MD-Ausgabe. Datum, Uhrzeit, Outlook-Titel, Einladungsteilnehmer und
Autor stehen in der neuen Seite. Aufgaben erhalten native OneNote-Checkboxen.
Audio und Rohtranskript werden nicht übertragen.

Nach Erfolg zeigt der Client **In OneNote gespeichert**, **OneNote öffnen** und
**Meeting-Fenster schließen**. Aufgaben bleiben im Client bearbeitbar: Verantwortliche,
Titel, Empfänger, Termin, offene Angaben und Auswahl. Änderungen werden automatisch
auf derselben Seite gespeichert. Zusammenfassung und Abhaken erfolgen in OneNote.
Der Client synchronisiert fremde Änderungen nicht zurück in den lokalen Entwurf.

## Aufgaben nachträglich korrigieren ab 0.40.6

Die lokale Korrektur wird zuerst dauerhaft gesichert. Der Client liest die Seite
mit Graph-Element-IDs und aktualisiert nur die geänderten Aufgabenabsätze. Abgehakte
Checkboxen bleiben abgehakt. Fremde Absätze, andere Aufgaben und die Zusammenfassung
werden nicht ersetzt. Bereits mit 0.40.5 angelegte Seiten werden anhand ihres
Meetingmarkers und eindeutig passenden ursprünglichen Aufgabenabsätzen zugeordnet.
Bei geänderten oder mehrdeutigen Absätzen wird die Übertragung angehalten und ein
Hinweis angezeigt; die lokale Korrektur bleibt erhalten. In diesem Fall die Angaben
direkt in OneNote ergänzen und anschließend **Aufgabenstatus prüfen** wählen.

Ausstehende Änderungen und Fehler stehen im Speicherstatus. **Meeting-Fenster
schließen** lässt ausstehende Übertragungen im Hintergrund weiterlaufen. Nach einem
Neustart werden noch nicht gesendete Korrekturen weiter übertragen. Bei unklaren
PATCH-Antworten oder einem Abbruch während des Versands prüft **Aufgabenstatus prüfen**
nur den vorhandenen Inhalt und wiederholt den Schreibaufruf nicht. Auch teilweise
ausgeführte Aktualisierungen werden so nicht blind wiederholt. Für eindeutig
abgelehnte Anforderungen gibt es **Aufgaben erneut übertragen**.

Technische Grenze: Graph bietet für OneNote keine dokumentierte atomare
Vergleichsbedingung für diese PATCH-Aufrufe. Eine exakt gleichzeitige Änderung am
selben Aufgabenabsatz zwischen Lesen und Schreiben lässt sich daher nicht sicher
ausschließen. Textkonflikte werden vor dem Schreiben erkannt; Formatierungen im
aktualisierten Absatz können neu aufgebaut werden. Links und Anhänge im betroffenen
Absatz führen vorsorglich zum Konflikt. Unveränderte Absätze werden nicht angefasst.
API: https://learn.microsoft.com/en-us/graph/onenote-update-page

## Notizbuchauswahl und Zugriff

Persönliche/geteilte OneNote-Bücher und die Notizbücher eigener Microsoft-365-Gruppen
werden zusammen angeboten; Abschnittsgruppen werden aufgelöst. Dafür verwendet
der Client die bestehenden delegierten Rechte `User.Read` und `Notes.ReadWrite.All`.
Teilweise fehlgeschlagene Abfragen werden angezeigt. Zusätzliche SharePoint-Sites
werden derzeit nur über `ONENOTE_SITE_PATHS` und vorhandenes `Sites.Read.All` gelesen;
eine zusätzliche Zustimmung allein entdeckt keine weiteren Sites. Vollständigkeit
gegenüber allen in der Desktop-OneNote-App geöffneten Büchern ist nicht garantiert.

Ohne geeignetes Buch kann der Nutzer abbrechen und lokal speichern, ein anderes
vorhandenes Buch wählen oder in OneNote eines anlegen und die Liste neu laden.
Der Client legt keine Notizbücher automatisch an. Der Dialog enthält keinen
pauschalen SharePoint-Verbindungsbutton.

## Fehler, Neustarts und mehrere Clients

Ein dauerhafter Marker wird vor dem Erstellungsaufruf gesichert. Während Prüfung
und Übertragung sind Ziel und Notizen gegen Änderungen gesperrt. Bei eindeutiger
Ablehnung sind **Erneut versuchen**, Ziel ändern oder lokale Ablage möglich.
Bei Timeout oder unklarer Antwort bleibt die lokale Sicherung erhalten. **Status
prüfen** sucht eine bereits angelegte Seite anhand des Meetingmarkers und erzeugt
keine weitere Seite. Beim Neustart wird ein unklarer Versuch einmal geprüft.
Auch bei einem Abbruch unmittelbar vor dem Versand wird nicht blind neu gesendet.

Gemerkte Kundenziele überleben Neustarts. Alte Buchzuordnungen werden mit dem
bisher gespeicherten Abschnitt verwendet; fehlt dieser, muss das Ziel bestätigt
werden. Alte noch nicht übertragene Entwürfe aus Versionen ohne automatische
Ablage werden beim Update lokal gesichert und nicht nachträglich hochgeladen.
Das interne Schema ist jetzt Version 9; ältere Clients können es nicht laden.

Verschiedene Clients können weiterhin eigene Seiten für dasselbe Meeting erzeugen.
Diese Doppelungen müssen ohne Platform manuell aufgeräumt werden. Kein Client
löscht oder überschreibt die Seite eines anderen Clients.

## Prüfen

1. Ohne Kundenzuordnung ein Meeting beenden: automatisch lokal gespeichert.
2. Während eines Meetings OneNote-Ziel wählen, Fertig: nach Abschluss neue Seite.
3. Kunde merken, App neu starten, weiterer Termin: dasselbe Notizbuch und derselbe Abschnitt.
4. Früheres lokales Meeting öffnen, nach OneNote verschieben, Abbrechen: Datei bleibt.
5. Verschieben bestätigen: neue Seite, danach keine unveränderte lokale MD-Datei mehr.
6. Übertragung unterbrechen: lokale Sicherung und unklarer Status; Prüfung ohne zweite Seite.

Automatisierte Tests verwenden simulierte Graph-Antworten und erzeugen keine
Seiten im Microsoft-365-Konto. Erkennung und echte Berechtigungen bleiben beim
nächsten regulären Meeting zu prüfen.

Der Speicherort bleibt in der Meeting-Ansicht unter dem Titel. Vergrößern und
Verkleinern ändern ausschließlich die Größe des Zusammenfassungsbereichs und des
Fensters. Aufgaben und Ablage bleiben erreichbar; die OneNote-Auswahl wird dabei
nicht neu geladen oder verworfen.
