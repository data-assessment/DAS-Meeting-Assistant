"""Native transient UI. No browser cache, persistence, export controls or file logging."""
import os
import time
import tkinter as tk
from tkinter import ttk
from .session import Session, validate
from .diagnostics import preferred_device, input_status
from .mai import MaiSession
from .mixed import MixedSession

MIX_MODE = "Azure Speech Mix (live, 1 Stream)"
LABELS = {"mic": "Mikrofon", "loopback": "Wiedergabe", "mixed": "Mix"}

class App:
    def __init__(self, root, demo=False, smoke=False):
        self.root = root
        root.title("DAS Speech-Test – MIX / Azure / MAI")
        root.geometry("1000x740")
        self.session = None
        self.ui_error = False
        self.revision = -1
        self.stopped_at = None
        self.cleared = False
        self.mics, self.loops = [], []
        self.region = tk.StringVar(value=os.getenv("SPEECH_REGION", "westeurope"))
        self.provider = tk.StringVar(value=MIX_MODE)
        self.key = tk.StringVar(value=os.getenv("SPEECH_KEY", ""))
        self.language = tk.StringVar(value="de-DE")
        self.name = tk.StringVar(value="Ich")
        self.status = tk.StringVar(value="Bereit. Aufnahme beginnt erst mit Start.")
        self.metrics = tk.StringVar(value="")
        panel = ttk.Frame(root, padding=16)
        panel.pack(fill="both", expand=True)
        ttk.Label(panel, text="Speech-Test: Mix / getrennte Spuren / MAI", font=("Segoe UI", 16)).pack(anchor="w")
        ttk.Label(panel, text="Testmodus: Audio direkt zu Azure. Keine Audio- oder Transkriptdateien.").pack(anchor="w", pady=(4,12))
        form = ttk.Frame(panel)
        form.pack(fill="x")
        self.inputs = []
        for row, (label, variable) in enumerate([
            ("Azure-Region", self.region), ("Speech-Schlüssel (nur für diesen Lauf)", self.key),
            ("Sprache", self.language), ("Lokaler Name (selbst angegeben)", self.name),
        ]):
            ttk.Label(form, text=label).grid(row=row, column=0, sticky="w", pady=3)
            entry = ttk.Entry(form, textvariable=variable, show="*" if variable is self.key else "")
            entry.grid(row=row, column=1, sticky="ew", pady=3)
            self.inputs.append(entry)
        form.columnconfigure(1, weight=1)
        ttk.Label(form, text="Mikrofon").grid(row=4, column=0, sticky="w", pady=3)
        self.mic = ttk.Combobox(form, state="readonly")
        self.mic.grid(row=4,column=1,sticky="ew")
        ttk.Label(form, text="Wiedergabe / Headset (Loopback)").grid(row=5,column=0,sticky="w",pady=3)
        self.loop = ttk.Combobox(form, state="readonly")
        self.loop.grid(row=5,column=1,sticky="ew")
        ttk.Label(form, text="Wiedergabe muss zum Lautsprecher in Teams passen. Windows-Standard kann davon abweichen.",
                  wraplength=740).grid(row=6, column=1, sticky="w", pady=(4, 0))
        ttk.Label(form, text="Transkriptionsdienst").grid(row=7, column=0, sticky="w", pady=4)
        self.provider_box = ttk.Combobox(form, textvariable=self.provider, state="readonly",
            values=[MIX_MODE, "Azure Speech (live)", "MAI-Transcribe-2 (nach Stop)"])
        self.provider_box.grid(row=7, column=1, sticky="ew", pady=4)
        self.provider_box.bind("<<ComboboxSelected>>", self.change_provider)
        self.mode_hint = tk.StringVar()
        ttk.Label(form, textvariable=self.mode_hint, wraplength=740).grid(row=8, column=1, sticky="w")
        self.update_mode_hint()
        buttons = ttk.Frame(panel)
        buttons.pack(fill="x", pady=10)
        self.refresh_button=ttk.Button(buttons,text="Geräte neu laden",command=self.refresh)
        self.refresh_button.pack(side="left",padx=(0,6))
        self.start_button=ttk.Button(buttons,text="Start",command=self.start)
        self.start_button.pack(side="left",padx=6)
        self.stop_button=ttk.Button(buttons,text="Stop",command=self.stop,state="disabled")
        self.stop_button.pack(side="left",padx=6)
        ttk.Button(buttons,text="Transkript verwerfen",command=self.discard).pack(side="left",padx=6)
        self.demo_button=ttk.Button(buttons,text="Offline-Demo",command=self.demo)
        self.demo_button.pack(side="left",padx=6)
        ttk.Label(panel,textvariable=self.status,wraplength=940).pack(anchor="w",pady=4)
        ttk.Label(panel,textvariable=self.metrics,wraplength=940).pack(anchor="w",pady=4)
        transcript_frame=ttk.Frame(panel)
        transcript_frame.pack(fill="both",expand=True)
        self.text=tk.Text(transcript_frame,wrap="word",state="disabled",undo=False,font=("Segoe UI",11))
        self.text.pack(side="left",fill="both",expand=True)
        scrollbar=ttk.Scrollbar(transcript_frame,command=self.text.yview)
        scrollbar.pack(side="right",fill="y")
        self.text.configure(yscrollcommand=scrollbar.set)
        ttk.Label(panel,wraplength=940,text=(
            "Sprecher-IDs gelten nur innerhalb dieser Sitzung und Quelle. Im Mix sind alle Sprecher anonym, auch du selbst. "
            "Loopback erfasst auch andere PC-Töne. Teams-Stummschaltung wird noch nicht berücksichtigt. "
            "Zeiten beziehen sich auf die jeweilige Audiospur; keine exakte Synchronisation. "
            "Nach Stop wird die Anzeige spätestens nach 5 Minuten verworfen."
        )).pack(anchor="w",pady=(10,0))
        root.protocol("WM_DELETE_WINDOW", self.close)
        def report_error(*args):
            self.ui_error = True
            self.status.set("Oberflächenfehler; Sitzung stoppen und Prototyp neu starten.")
        root.report_callback_exception=report_error
        if not smoke and not demo:
            self.refresh()
        if demo:
            self.demo()
        self.tick()

    def update_mode_hint(self):
        if self.provider.get() == MIX_MODE:
            value = "MIX: 1 Azure-Verbindung; Mikrofon + Wiedergabe gemischt. Alle Sprecher anonym. Max. 5 Minuten. Beide Eingänge je 50 % Pegel."
        elif self.provider.get().startswith("MAI"):
            value = "MAI: North-Europe-Schlüssel, automatische Sprache, max. 3 Minuten RAM-Aufnahme; Text erst nach Stop."
        else:
            value = "Azure getrennt: 2 Verbindungen; Mikrofonname lokal zugeordnet, Sprechertrennung für Wiedergabe. Max. 20 Minuten."
        self.mode_hint.set(value)

    def change_provider(self, event=None):
        self.region.set("northeurope" if self.provider.get().startswith("MAI") else "westeurope")
        self.key.set("")

        self.update_mode_hint()

    def refresh(self):
        from .capture import devices
        try:
            previous = {}
            for source, widget, candidates in [("mic", self.mic, self.mics), ("loopback", self.loop, self.loops)]:
                index = widget.current()
                previous[source] = candidates[index] if 0 <= index < len(candidates) else None
            found = devices()
            self.mics=[d for d in found if not d.get("isLoopbackDevice")]
            self.loops=[d for d in found if d.get("isLoopbackDevice")]
            for source, widget, candidates in [("mic",self.mic,self.mics),("loopback",self.loop,self.loops)]:
                widget["values"]=[f"{int(d['index'])}: {d['name']} ({int(d['defaultSampleRate'])} Hz)" +
                                  (" [Windows-Standard]" if d.get("isSystemDefault") else "") for d in candidates]
                index = preferred_device(candidates, previous[source])
                if index >= 0:
                    widget.current(index)
                else:
                    widget.set("")
            self.status.set("Geräte mit Teams abgleichen. Aufnahme beginnt erst mit Start.")
        except Exception:
            self.status.set("WASAPI-Geräte konnten nicht gelesen werden. Headset und Audiotreiber prüfen.")

    def busy(self, active):
        for item in self.inputs:
            item.configure(state="disabled" if active else "normal")
        for item in (self.mic,self.loop,self.provider_box):
            item.configure(state="disabled" if active else "readonly")
        for item in (self.start_button,self.refresh_button,self.demo_button):
            item.configure(state="disabled" if active else "normal")
        self.stop_button.configure(state="normal" if active else "disabled")

    def start(self):
        try:
            validate(self.region.get().strip(),self.key.get(),self.language.get().strip(),self.name.get().strip())
            if self.mic.current()<0 or self.loop.current()<0:
                raise ValueError("Bitte Mikrofon und Wiedergabegerät auswählen.")
            if self.session:
                self.session.transcript.clear()
            factory = (MixedSession if self.provider.get() == MIX_MODE else
                       MaiSession if self.provider.get().startswith("MAI") else Session)
            self.session=factory(self.name.get().strip())
            self.revision=-1
            self.stopped_at=None
            self.cleared=False
            self.busy(True)
            self.session.start(self.region.get().strip(),self.key.get(),self.language.get().strip(),
                {"mic":self.mics[self.mic.current()],"loopback":self.loops[self.loop.current()]})
            self.key.set("")
        except ValueError as exc:
            self.status.set(str(exc))
            self.busy(False)
        except Exception:
            self.status.set("Start fehlgeschlagen. Eingaben und optionale Speech-Abhängigkeiten prüfen.")
            self.busy(False)

    def stop(self):
        if self.session:
            self.session.stop()
            self.status.set("Aufnahme wird beendet; Ergebnisse werden verarbeitet.")
            self.stop_button.configure(state="disabled")

    def discard(self):
        if self.session:
            if isinstance(self.session, MaiSession):
                self.session.discard()
            else:
                self.session.stop()
                self.session.transcript.clear()
        self.cleared=True
        self.render("")
        self.status.set("Transkript verworfen.")

    def demo(self):
        if self.session:
            self.session.transcript.clear()
        self.session=Session(self.name.get().strip() or "Ich")
        for source,speaker,text,offset in [
            ("mic",None,"Ich erstelle bis Freitag das Angebot.",0),
            ("loopback","Guest-1","Der Pilot startet am Montag.",2),
            ("loopback","Guest-2","Die Teilnehmerzahl klären wir morgen.",5),
        ]:
            self.session.transcript.add(source,speaker,text,offset)
        self.session.transcript.seal()
        self.session.phase="Offline-Demo: synthetische Daten, keine Aufnahme und keine Azure-Verbindung."
        self.session.finished.set()
        self.revision=-1
        self.stopped_at=None
        self.cleared=False

    def render(self,value):
        self.text.configure(state="normal")
        self.text.delete("1.0","end")
        self.text.insert("1.0",value)
        self.text.configure(state="disabled")

    def tick(self):
        if self.session:
            state=self.session.snapshot()
            if not self.cleared:
                self.status.set(state["phase"] + (" — "+state["error"] if state["error"] else ""))
            revision,finals,partials=self.session.transcript.snapshot()
            stats=[]
            for source in dict.fromkeys([*state["metrics"], *state["buffers"]]):
                label=LABELS[source]
                metric=state["metrics"].get(source,{})
                level=metric.get("dbfs",-100)
                status=input_status(metric,time.monotonic())
                count=sum(item.source==source for item in finals)
                recognition=f"{count} Textbeiträge" if count else "noch kein Azure-Text"
                if isinstance(self.session, MixedSession) and source != "mixed":
                    recognition="lokaler Eingang zum Mix"
                stats.append(f"{label}: {status} ({level:.0f} dBFS) | {recognition}")
                if source=="loopback" and status!="Audiosignal vorhanden" and (state["phase"].startswith("Läuft") or state["phase"].startswith("MAI: Aufnahme")):
                    stats.append("Wenn die andere Person spricht: Stop drücken und das Teams-Lautsprechergerät als Loopback auswählen.")
            self.metrics.set("\n".join(stats))
            if revision!=self.revision:
                self.revision=revision
                rows=[]
                for item in finals:
                    label=LABELS[item.source]
                    rows.append(f"[{label} {item.audio_offset:07.2f}s] {item.speaker}\n{item.text}\n")
                for item in partials.values():
                    rows.append(f"[vorläufig / {item.source}] {item.speaker}: {item.text}\n")
                self.render("\n".join(rows))
            if self.session.finished.is_set():
                self.busy(False)
                if self.stopped_at is None:
                    self.stopped_at=time.monotonic()
                if time.monotonic()-self.stopped_at>=300 and not self.cleared:
                    self.discard()
        self.root.after(200,self.tick)

    def close(self):
        self.key.set("")
        if self.session:
            if isinstance(self.session, MaiSession):
                self.session.discard()
            else:
                self.session.stop()
                self.session.transcript.clear()
        self.render("")
        self.root.destroy()

def main():
    import argparse
    parser=argparse.ArgumentParser(description="DAS Azure-Speech-Prototyp ohne Audio-/Transkriptdateien")
    parser.add_argument("--demo",action="store_true",help="Synthetische Offline-Demo ohne Audio oder Azure")
    parser.add_argument("--smoke-test",action="store_true",help="Verdeckter GUI-Starttest ohne Audio oder Azure")
    args=parser.parse_args()
    root=tk.Tk()
    if args.smoke_test:
        root.withdraw()
    app=App(root,demo=args.demo or args.smoke_test,smoke=args.smoke_test)
    if args.smoke_test:
        root.after(300,app.close)
    root.mainloop()
    if args.smoke_test and app.ui_error:
        raise SystemExit(1)
