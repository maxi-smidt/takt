# TAKT

**TAKT** ist eine vollständig lokale Stoppuhr für
Feuerwehr-Training und Bewerbsläufe. Die Anwendung ist für einen Raspberry Pi 3
mit großem, normalerweise offenem Taster ausgelegt und lässt sich auf einem
Laptop vollständig simulieren.

## Aktueller Stand

Der erste funktionsfähige Stand enthält:

- die Zustände Bereit, Läuft, Gestoppt, Gespeichert und Verwerfen bestätigen,
- genaue Messung mit monotoner Uhr und Speicherung in Millisekunden,
- Zuschläge in 5- oder 10-Sekunden-Schritten, die wieder reduziert werden können,
- lokale, transaktionale Speicherung und tägliche Sicherung,
- heutige Läufe, Bestzeiten und jeden Lauf als eigenen Diagrammpunkt,
- einen fokussierten Vollflächen-Timer während eines laufenden Durchgangs,
- Einstellungen zur Korrektur von Zuschlägen und zum Löschen fehlerhafter Läufe,
- Tastatur-, Maus- und Mock-Taster-Steuerung,
- optionalen sicht- und hörbaren Summer-Mock,
- eine von der Oberfläche getrennte GPIO-Eingabe,
- einen lokalen Webserver mit synchroner Bedienung über mehrere Geräte,
- eine responsive React-Browser-Oberfläche mit derselben deutschen Bedienlogik,
- einen Display-Wachschutz für Tablets und Laptops, auch beim Zugriff über HTTP,
- ein optionales Startsignal über AUX oder einen gekoppelten Bluetooth-Lautsprecher
  mit einstellbarer Startverzögerung,
- eine automatisierte, headless Raspberry-Pi-Installation mit `takt.local`
  und Systemdienst.

## Raspberry Pi mit einem Skript einrichten

Empfohlen wird **Raspberry Pi OS Lite 64-bit** (aktuelle stabile Version).
Ein Desktop, Chromium, Bildschirm, Autologin oder lokal gestartete Oberfläche
sind nicht erforderlich. `uname -m` muss `aarch64` ausgeben. Das Projekt muss
auf dem Pi beispielsweise unter `/home/msmidt/takt` liegen.

Im Projektverzeichnis genügt:

```bash
./scripts/install_raspberry_pi.sh
```

Alternativ kann der Laptop das Projekt übertragen und anschließend das komplette
Setup auf dem Pi ausführen. Beim ersten Mal gilt üblicherweise:

```bash
./scripts/deploy_to_raspberry_pi.sh msmidt@raspberrypi.local
```

Nach der Einrichtung lautet dieselbe Adresse:

```bash
./scripts/deploy_to_raspberry_pi.sh msmidt@takt.local
```

Damit bleibt für Installation und spätere Updates jeweils ein einziger
Laptop-Befehl. Eventuelle SSH- und `sudo`-Passwortabfragen erscheinen während
dieses einen Ablaufs.

Das Skript installiert nur die benötigten System- und Python-Pakete, richtet
`lgpio`, das auf Lite fehlende PipeWire-/Bluetooth-Audio, `takt.service` und
die lokale Adresse ein. PipeWire läuft über eine dauerhafte Benutzersitzung
auch dann, wenn niemand am Pi angemeldet ist. Ein dauerhafter Bluetooth-Agent
autorisiert das PIN-lose „Just Works“-Pairing von Lautsprechern ohne lokalen
Desktop. Eine vorhandene Konfiguration oder Laufdaten werden nicht
überschrieben. Alte TAKT-Kiosk-Autostarts werden bei einer Migration entfernt.
Am Ende bietet das Skript einen Neustart an.

Danach ist TAKT auf Geräten im selben lokalen Netzwerk erreichbar:

```text
http://takt.local
```

Die IP-Adresse darf sich ändern; `takt.local` wird über mDNS aufgelöst. Falls
ein Gerät `.local` nicht unterstützt, empfiehlt sich zusätzlich eine
DHCP-Reservierung im Router.

Alle Browser und der physische GPIO-Taster steuern denselben autoritativen
Timerprozess. Änderungen erscheinen über WebSockets unmittelbar auf allen
verbundenen Bildschirmen. Da jeder Client im lokalen Netz TAKT bedienen und
auch das Herunterfahren bestätigen kann, gehört TAKT in ein vertrauenswürdiges,
nicht öffentliches WLAN.

Die Browser-Oberfläche hält den Bildschirm nach der ersten Berührung, dem
ersten Klick oder Tastendruck aktiv. Unter HTTPS verwendet sie den nativen
Screen Wake Lock des Browsers; beim normalen Zugriff über `http://takt.local`
läuft als kompatibler Fallback ein winziges, stummes Video. Der Status steht
unten in der Leiste unter **ANZEIGE**. Energiesparmodi oder Gerätevorgaben können
den Wachschutz weiterhin übersteuern. Nach einem App- oder Tabwechsel fordert
TAKT den Wachschutz beim Zurückkehren automatisch erneut an.

Das Installationsskript kann nach einem Programmupdate erneut ausgeführt werden.
Der Deployment-Befehl entfernt veraltete Projektdateien auf dem Pi, behält
virtuelle Umgebung, Konfiguration und Laufdaten und startet den Server nach
einem erfolgreichen Update ausdrücklich neu.

## Mehrere Raspberry Pis zentral verwalten

TAKT enthält eine selbst gehostete **TAKT Fleet Registry**. Sie läuft auf dem
Laptop beziehungsweise einem ständig eingeschalteten Rechner und bietet eine
Browser-Oberfläche für:

- Online-/Offline-Status, TAKT-Version, Timerzustand, Speicher und Temperatur,
- das Hinterlegen mehrerer benannter TAKT-Versionen,
- die gezielte Installation einer Version auf einem bestimmten Raspberry Pi,
- Neustart des TAKT-Dienstes,
- den Fortschritt und das Ergebnis jedes Remote-Auftrags,
- eine automatisch aktualisierte Kopie der Laufdatenbank jedes Geräts.

Die Raspberry Pis bauen ausschließlich ausgehende HTTP(S)-Verbindungen zur
Registry auf. Nach der einmaligen Einrichtung ist deshalb kein SSH-Zugriff mehr
notwendig. TAKT und der physische Taster funktionieren auch dann vollständig
lokal weiter, wenn WLAN oder Registry vorübergehend nicht verfügbar sind.

### Registry auf Unraid mit Docker starten

Die Registry wird auf Unraid als eigener, nicht privilegierter Container
betrieben. Datenbank, Releases, Spiegelungen und der lokale Schlüssel bleiben
außerhalb des Containers unter `/mnt/user/appdata/takt/registry-data` erhalten.
Der Unraid-`appdata`-Share sollte einen Pool als **Primary storage** und
**Secondary storage: None** verwenden. Vor einem bewussten Verschieben mit dem
Mover muss der Container gestoppt werden.

#### Variante A: Unraid-Docker-Oberfläche

Das Repository enthält `unraid/takt-registry.xml` als Unraid-Template. Es wird
einmalig registriert mit:

```bash
mkdir -p /mnt/user/appdata/takt/registry-data
chown 10001:10001 /mnt/user/appdata/takt/registry-data
chmod 0700 /mnt/user/appdata/takt/registry-data
cp /mnt/user/appdata/takt/unraid/takt-registry.xml \
  /boot/config/plugins/dockerMan/templates-user/my-takt-registry.xml
```

Danach unter **Docker → Add Container** das Template `takt-registry` auswählen,
ein langes Adminpasswort setzen und den Datenpfad kontrollieren. Das Template
nutzt `ghcr.io/maxi-smidt/takt-registry:latest`, läuft als fest definierter
nicht privilegierter Benutzer und erscheint wie andere Container in der
Unraid-Oberfläche. Nach dem ersten erfolgreichen GitHub-Workflow muss das
GHCR-Paket einmalig auf **Public** gestellt und ein anonymer Pull geprüft werden.
Commit-Tags sind
unveränderliche Bezugspunkte, während `latest` und `major.minor` bewusst
weiterwandern.

#### Variante B: Unraid Compose Manager

Unraid bringt Docker Compose nicht nativ mit; diese Variante verwendet das
Compose-Manager-Plugin. Die Compose-Datei baut auf dem NAS nichts selbst,
sondern zieht standardmäßig das in GitHub Actions geprüfte Multi-Architektur-
Image `ghcr.io/maxi-smidt/takt-registry:latest`.

Nach dem Klonen des Repositories im Unraid-Terminal:

```bash
cd /mnt/user/appdata/takt
cp .env.example .env
mkdir -p /mnt/user/appdata/takt/registry-data
chown 10001:10001 /mnt/user/appdata/takt/registry-data
chmod 0700 /mnt/user/appdata/takt/registry-data
chmod 0600 .env
```

In `.env` mindestens `TAKT_REGISTRY_ADMIN_PASSWORD` durch ein eigenes langes
Passwort ersetzen. UID/GID `10001` gehören dem bewusst nicht privilegierten
Benutzer im Container; die beiden Befehle geben ihm Schreibzugriff auf das
Registry-Datenverzeichnis. Anschließend Image ziehen und Stack starten:

```bash
docker compose pull
docker compose up -d
docker compose ps
```

Für eine lokale Entwicklung ohne GHCR-Pull kann der mitgelieferte Override
verwendet werden. Er baut dasselbe Multi-Stage-Dockerfile auf dem Rechner:

```bash
docker compose -f compose.yaml -f compose.local.yaml up --build -d
```

Die Standarddatei bleibt absichtlich pull-only. `latest` ist ein beweglicher
Main-Branch-Stand; für reproduzierbare Unraid-Deployments einen Commit- oder
Versions-Tag in `TAKT_REGISTRY_IMAGE` verwenden.

Im Compose-Manager-Plugin wird dieses Verzeichnis als Stack importiert. Für
`TAKT_REGISTRY_IMAGE` kann `latest`, ein Versionstag wie `0.2.0` oder ein
unveränderlicher `sha-...`-Tag eingetragen werden. Mit
`TAKT_REGISTRY_PULL_POLICY=always` prüft Compose bei jedem Start auf ein
aktualisiertes Image.

Die Oberfläche ist danach unter `http://UNRAID-IP:8090` erreichbar. Der
Container startet nach einem NAS-Neustart automatisch wieder. Ein Update der
Registry erfolgt mit:

```bash
cd /mnt/user/appdata/takt
git pull
docker compose pull
docker compose up -d
```

Im Compose-Manager entspricht das **Pull** und anschließend **Compose Up**.
Anwendungscode wird nicht auf dem NAS gebaut; aus dem Klon werden nur
`compose.yaml` und `.env` für den Stack benötigt.

`docker compose down` entfernt nur den Container und das Netzwerk; die unter
`TAKT_REGISTRY_DATA_PATH` gespeicherten Registry-Daten bleiben erhalten. Der
Pfad sollte in die normale Unraid-Appdata-Sicherung aufgenommen werden. Die
Registry erstellt zusätzlich täglich eine konsistente Online-Sicherung der
Metadaten unter `registry-data/backups/`. Für ein vollständiges externes Backup
einschließlich Releases und Spiegelhistorie den Container während des
Appdata-Backups stoppen oder ein Backup-Werkzeug verwenden, das Container
automatisch anhält. `job-secrets.key` muss zusammen mit `registry.db`
gesichert und wiederhergestellt werden; ohne diesen Schlüssel können noch
offene WLAN-Aufträge nicht entschlüsselt werden.

Diagnosebefehle:

```bash
docker compose ps
docker compose logs --tail=100 registry
curl --fail http://127.0.0.1:8090/health
```

Der Healthcheck prüft Datenbank- und Schema-Zugriff sowie die letzte
konsistente Sicherung. Docker-Logs rotieren automatisch.

### Automatisch veröffentlichte Artefakte

Der Workflow `.github/workflows/registry-image.yml` läuft auf `main`, auf
Versionstags (`v*`) und manuell:

- Ruff, Pytest, ESLint und der Registry-UI-Build müssen zuerst erfolgreich
  sein.
- Danach wird `ghcr.io/maxi-smidt/takt-registry` für `linux/amd64` und
  `linux/arm64` veröffentlicht. `latest` folgt `main`; zusätzlich gibt es
  `sha-...` und bei Versionstags beispielsweise `0.2.0` sowie `0.2`.
- Das Raspberry-Pi-Paket wird zusammen mit Prüfsumme und JSON-Manifest für 30
  Tage als Actions-Artefakt abgelegt.
- `compose.yaml`, die Environment-Vorlage und das Unraid-XML werden zusätzlich
  als kleiner Unraid-Stack-Artefakt veröffentlicht.
- Ein Tag wie `v0.2.0` muss exakt zur Version in `pyproject.toml` passen und
  erzeugt eine dauerhafte GitHub Release mit Pi-Paket, Prüfsumme, Manifest,
  Unraid-Stack-Dateien und dem exakten Digest des Registry-Images.

Vor dem ersten Unraid-Pull muss das einmalig erzeugte GHCR-Paket in GitHub auf
**Public** gestellt werden. Danach benötigt der Unraid-Stack keine GitHub-
Anmeldedaten.

### Registry auf dem Laptop starten

Die Registry benötigt ein Administratorpasswort mit mindestens zehn Zeichen:

```bash
TAKT_REGISTRY_ADMIN_PASSWORD='ein-langes-eigenes-passwort' \
  ./scripts/launch_registry.sh
```

Danach ist sie standardmäßig unter `http://127.0.0.1:8090` und über die
WLAN-IP-Adresse des Rechners auf Port 8090 erreichbar. Der Rechner muss für
Statusmeldungen, Spiegelungen und Remote-Aufträge eingeschaltet und von den Pis
erreichbar sein. Soll die Registry ständig verfügbar sein, kann derselbe Dienst
stattdessen auf einem NAS, Mini-PC oder eigenen Verwaltungs-Pi laufen; bedient
wird er weiterhin im Browser des Laptops.

Die Registry speichert ihre Daten standardmäßig unter
`~/.local/share/takt-registry/`:

```text
registry.db       Geräte, Versionen und Auftragsverlauf
job-secrets.key   Schlüssel für verschlüsselte Zugangsdaten offener Aufträge
releases/         hochgeladene TAKT-Pakete
mirrors/          versionierte SQLite-Snapshots der Raspberry Pis
backups/          tägliche konsistente Registry-Metadaten-Sicherungen
```

### Raspberry Pi einmalig verbinden

1. In der Registry **Enroll device** wählen, Gerätename, Hostname, SSH-Benutzer,
   Pi-Adresse und Registry-Adresse eintragen und den erzeugten Befehl kopieren.
2. Eine vom Raspberry Pi erreichbare Registry-Adresse verwenden. `localhost`
   ist dafür ungeeignet; im lokalen WLAN ist das beispielsweise
   `http://192.168.1.20:8090`.
3. Den bestehenden Deployment-Befehl einmal mit Registry-Daten ausführen:

```bash
TAKT_REGISTRY_URL='http://192.168.1.20:8090' \
TAKT_REGISTRY_ALLOW_INSECURE_HTTP='true' \
TAKT_ENROLLMENT_CODE='TAKT-...' \
TAKT_DEVICE_NAME='Bahn 1' \
TAKT_HOSTNAME='takt-01' \
  ./scripts/deploy_to_raspberry_pi.sh msmidt@raspberrypi.local
```

Der Agent lehnt eine entfernte HTTP-Adresse standardmäßig ab. Bei einer
HTTP-Adresse zeigt der Assistent deshalb eine gesonderte Warnung und erzeugt
die `TAKT_REGISTRY_ALLOW_INSECURE_HTTP`-Zeile erst nach ausdrücklicher
Bestätigung. Sie ist nur für HTTP über ein privates VPN oder ein bewusst
isoliertes, vertrauenswürdiges LAN gedacht; bei HTTPS entfällt sie.

Der Code ist 60 Minuten gültig. Das Installationsskript meldet den Agenten
direkt nach dem Erstellen seiner Umgebung an, bevor die langsamere Audio- und
Systemkonfiguration weiterläuft. Die Pi-Seite erzeugt ihren Geräteschlüssel vor
der Anmeldung; geht die Antwort verloren, kann dieselbe Anmeldung sicher
wiederholt werden. Nach Erfolg wird der Einmalcode aus `agent.toml` entfernt.

Wird ein Gerät in der Registry widerrufen, ist sein bisheriges Bearer-Token
sofort ungültig. Zum erneuten Verbinden die lokale Agentenidentität und die
Konfiguration auf dem Pi entfernen, danach in der Registry einen neuen Code
erstellen und den einmaligen Installationsbefehl erneut ausführen:

```bash
sudo systemctl stop takt-agent.service
rm ~/.config/takt/agent.toml ~/.config/takt/agent-identity.json
```

Die TAKT-Laufdaten und die Registry-Spiegel bleiben erhalten; nur die
Verbindung wird neu ausgestellt.

Für weitere Geräte werden ein neuer Code, ein eigener Anzeigename und ein
eindeutiger Hostname (`takt-02`, `takt-03`, …) verwendet. Identität und
Zugangsdaten des Agenten bleiben auf dem Pi erhalten. Spätere TAKT-Versionen
werden nur noch über die Registry installiert.

### Ein WLAN auf einem Pi speichern

Auf der Gerätekarte öffnet **Add Wi-Fi** ein Formular für SSID und
WPA/WPA2-Passwort. Die Registry legt daraus einen gerätespezifischen Auftrag
an. Das Passwort erscheint weder im Auftragsverlauf noch in den
Registry-Datenbanksicherungen; solange der Auftrag offen ist, liegt es mit
`job-secrets.key` authentifiziert verschlüsselt in der Registry. Deshalb muss
auch dieser Schlüssel in die externe Sicherung des Registry-Datenverzeichnisses
aufgenommen werden.

Der Pi speichert ein ausschließlich von TAKT verwaltetes NetworkManager-Profil
mit der Standardpriorität `0`. Ein erneuter Auftrag für dieselbe SSID
aktualisiert dasselbe Profil. Bestehende fremde Profile werden nicht verändert.
Der Auftrag aktiviert die Verbindung nicht und trennt daher auch nicht die
aktuelle Registry-Verbindung; NetworkManager berücksichtigt das neue Profil bei
einer späteren automatischen Verbindungswahl.

Das Installationsskript richtet dafür einen eng begrenzten Root-Helfer ein,
wenn NetworkManager aktiv ist. Bereits verbundene Pis benötigen deshalb einmal
erneut `deploy_to_raspberry_pi.sh` beziehungsweise `install_raspberry_pi.sh`;
ein normales Anwendungs-Release aktualisiert den getrennt installierten Agenten
und dessen Systemberechtigung nicht. Fehlt **Add Wi-Fi** beziehungsweise ist die
Schaltfläche deaktiviert, NetworkManager und diese einmalige Installation
prüfen. Offene, Enterprise- und versteckte WLANs sind nicht Teil dieser
Funktion.

SSID und Passwort werden vom Browser zur Registry und anschließend zum Pi
übertragen. Dafür HTTPS oder ein privates VPN verwenden; HTTP schützt diese
Zugangsdaten nicht.

### Eine bestimmte TAKT-Version installieren

Zuerst das normale Raspberry-Pi-Paket erstellen:

```bash
./scripts/package_for_raspberry_pi.sh
```

Vor dem Paketieren muss die Version in `pyproject.toml` stimmen. In der Registry
anschließend **Add release** wählen, exakt dieselbe Versionsbezeichnung wie
`0.2.0` vergeben und `dist/takt-raspberry-pi.tar.gz` hochladen. Abweichende
Paket- und Eingabeversionen weist die Registry zurück. Auf der Karte des
gewünschten Pis kann genau diese Version ausgewählt und mit **Install**
beauftragt werden.

Der Agent:

1. wartet, bis TAKT im Zustand `ready` ist, damit kein laufender oder
   ungespeicherter Durchgang unterbrochen wird,
2. lädt das Paket über WLAN fortsetzbar in eine `.part`-Datei, prüft Größe und
   SHA-256-Prüfsumme und bereitet eine getrennte, versionierte Installation vor,
3. erstellt unmittelbar vorher eine SQLite-Sicherung,
4. erwirbt eine lokale Maintenance-Sperre, die atomar nur im Zustand `ready`
   gelingt; ein kurzlebiger, dauerhafter Marker hält diese Sperre auch über den
   Neustart hinweg und blockiert Browser- und GPIO-Starts,
5. schaltet atomar auf die gewählte Version um und startet nur `takt.service`
   neu,
6. prüft Version und `/health`; bei einem Fehler werden Symlink,
   Versionsdatei und Laufdatenbank auf den vorigen Stand zurückgesetzt.

Jede Aktivierungsphase wird vor dem nächsten Schritt fsync-sicher protokolliert.
Nach Prozessabsturz oder Stromausfall setzt der Agent deshalb vor dem nächsten
Registry-Auftrag entweder die gesunde Version fort oder stellt Version,
Datenbank und Dienst automatisch auf den vorigen Stand zurück.

Remote-Aufträge verwenden kurze, erneuerbare Leases. Der Agent speichert das
Endergebnis lokal, bis die Registry es bestätigt. WLAN-Ausfälle können dadurch
weder einen fertigen Auftrag vergessen noch einen Neustart unbemerkt doppelt
ausführen. Reconnects verwenden exponentielles Backoff mit Zufallsanteil, damit
nach einem NAS-Neustart nicht alle Pis gleichzeitig anfragen.

Die vorhandene Python-Umgebung wird für das Release kopiert. Solange sich die
Abhängigkeiten nicht ändern, benötigt ein Versionswechsel deshalb nur die
WLAN-Verbindung zur Registry und keinen Internetzugang. Neue Python- oder
Systemabhängigkeiten gehören weiterhin in das einmalig beziehungsweise bewusst
ausgeführte Installationsskript.

### Laufdaten spiegeln

Der Agent erstellt mit der SQLite-Backup-API eine konsistente Momentaufnahme,
sobald sich die lokale Datenbank geändert hat, und überträgt sie standardmäßig
innerhalb einer Minute. Die Registry prüft Prüfsumme, SQLite-Integrität und die
`runs`-Tabelle, bevor sie sie als unveränderlichen, per SHA-256 adressierten
Snapshot ablegt. Die letzten 48 Snapshots plus bis zu 30 tägliche Stände je Pi
bleiben erhalten; ein Upload überschreibt daher niemals die einzige gute Kopie.
In der Gerätekarte
werden Zeitpunkt, Größe und Anzahl der gespiegelten Läufe angezeigt; die Kopie
kann dort als `.sqlite3` heruntergeladen werden.

Das ist bewusst keine gemeinsam beschreibbare Datenbank: Der Raspberry Pi
bleibt die autoritative Quelle und kann bei einem Netzwerkausfall ohne
Abhängigkeit von der Registry weiter messen und speichern.

### Netzwerk und Sicherheit

Im vertrauenswürdigen lokalen WLAN kann die Registry zunächst über HTTP
betrieben werden. Über verschiedene Standorte oder das öffentliche Internet
darf weder die Registry noch die bisherige, nicht authentifizierte TAKT-Web-API
direkt freigegeben werden. Dafür ist ein privates WireGuard-/Tailscale-Netz oder
HTTPS mit einem gültigen Zertifikat vorgesehen. `takt-registry` akzeptiert dazu
`--tls-certificate` und `--tls-key`.

HTTP überträgt Adminpasswort, Gerätetoken und Spiegelungsdaten unverschlüsselt.
Die SHA-256-Prüfung erkennt Übertragungsfehler, authentifiziert über HTTP aber
keinen Release: Ein Angreifer im Netz könnte Auftrag und Paket gemeinsam
ersetzen. Remote-Installationen dürfen deshalb nur über HTTPS oder ein privates
VPN ausgeführt werden.
Der Pi-Agent verweigert Nicht-Loopback-HTTP daher ohne die explizite
Konfiguration `allow_insecure_http = true`. Diese Option bestätigt nur das
bewusst akzeptierte Risiko; sie macht HTTP nicht sicher.
Für einen dauerhaften Betrieb ist deshalb auch im WLAN HTTPS oder ein privates
Tailscale-/WireGuard-Netz vorzuziehen. Hinter einem HTTPS-Reverse-Proxy muss
`TAKT_REGISTRY_SECURE_COOKIES=true` gesetzt werden. Für eine private CA kann im
Pi-`agent.toml` `ca_bundle = "/pfad/ca.pem"` gesetzt werden; `verify_tls = false`
ist nur für eine kurzzeitige Diagnose gedacht.

Die Registry installiert derzeit TAKT-Anwendungsversionen. Vollständige,
ausfallsichere Raspberry-Pi-OS-Abbilder mit A/B-Rollback sind ein eigener
Update-Typ und nicht Bestandteil dieses ersten Registry-Stands.

## Transportpaket für den Raspberry Pi erstellen

Wenn das Projekt per USB-Stick, Netzlaufwerk oder manuell auf den Raspberry Pi
übertragen werden soll, genügt auf dem Laptop:

```bash
./scripts/package_for_raspberry_pi.sh
```

Das Skript baut zuerst die aktuelle React-Oberfläche und prüft anschließend, ob
Server, Installer und beide Audiodateien vorhanden sind. Danach erstellt es:

```text
dist/takt-raspberry-pi.tar.gz
dist/takt-raspberry-pi.tar.gz.sha256
```

Git-Daten, virtuelle Umgebung, Node-Module, Caches, Screenshots und lokale
Testdaten werden nicht eingepackt. Das Archiv enthält oben einen Ordner `takt`
und kann auf dem Raspberry Pi so installiert werden:

```bash
tar -xzf ~/takt-raspberry-pi.tar.gz -C ~
cd ~/takt
./scripts/install_raspberry_pi.sh
```

Ein eigener Ausgabepfad ist optional:

```bash
./scripts/package_for_raspberry_pi.sh /Pfad/zum/takt-paket.tar.gz
```

## Schnellstart auf dem Laptop

Voraussetzung ist Python 3.11 oder neuer.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/takt --windowed --mock-gpio --mock-buzzer
```

Alternativ übernimmt `./scripts/launch_dev.sh` Einrichtung und Start.

Der blaue **Mock-Taster** am unteren Rand verhält sich wie der spätere
Mushroom-Taster. Der **Summer-Mock** zeigt bei Start, Stopp, Speichern und
Verwerfen einen Statusimpuls und löst zusätzlich den Laptop-Systemton aus.
Beide Mock-Anzeigen erscheinen nur, wenn die zugehörigen Startargumente gesetzt
sind.

## Weboberfläche auf dem Laptop testen

Der Webserver lässt sich mit Mock-Taster und Mock-Summer starten:

```bash
./scripts/launch_web_dev.sh
```

Anschließend im Browser öffnen:

```text
http://127.0.0.1:8080
```

Der Entwicklungsschnellstart bindet den Server absichtlich nur an den Laptop
selbst. Auf dem Pi übernimmt das Installationsskript den Netzwerkzugriff.

### Browser-Oberfläche weiterentwickeln

Der React-Quellcode liegt unter `webui/`. Die fertig gebauten, vollständig
offline nutzbaren Dateien werden unter `src/takt/web/static/` abgelegt und
zusammen mit TAKT auf den Pi übertragen. Der Pi benötigt deshalb weder Node.js
noch eine Internetverbindung.

Nach Änderungen an der Browser-Oberfläche genügt:

```bash
./scripts/build_web_ui.sh
```

Das Skript installiert bei Bedarf die festgeschriebenen Pakete, prüft den
React-Code und erzeugt anschließend die produktiven Dateien.

### Startargumente

| Argument | Bedeutung |
|---|---|
| `--windowed` | Startet in einem normalen Fenster statt im Vollbild. |
| `--mock-gpio` | Verwendet und zeigt den blauen Laptop-Mock-Taster. |
| `--mock-buzzer` | Simuliert den Summer sichtbar und über den Systemton. |
| `--database PFAD` | Verwendet für einen isolierten Test eine andere Datendatei. |

`takt-server` unterstützt zusätzlich:

| Argument | Bedeutung |
|---|---|
| `--host ADRESSE` | Bindeadresse; produktiv `0.0.0.0`. |
| `--port PORT` | HTTP-Port; der Installer verwendet Port 80. |

## Bedienung

| Taste | Aktion |
|---|---|
| Leertaste | Lauf starten oder stoppen |
| `5` | 5 Sekunden Zuschlag |
| `0` | 10 Sekunden Zuschlag |
| Strg+`5` | 5 Sekunden vom Zuschlag abziehen |
| Strg+`0` | 10 Sekunden vom Zuschlag abziehen |
| Enter | gestoppten Lauf speichern / Verwerfen bestätigen |
| `R` oder Escape | Verwerfen anfordern |
| F11 | Vollbild umschalten |
| Strg+Q | Anwendung beenden |

Nach dem Stoppen verwirft ein Doppeldruck auf den Mock- oder GPIO-Taster
innerhalb von 600 ms den aktuellen Lauf. Ein einzelner Druck tut nichts.
Beim Reduzieren bleibt der Zuschlag immer mindestens bei `+00:00.00`; die
gemessene Ist-Zeit wird dabei nie verändert.

## Laufdaten kuratieren

Über **Einstellungen** können gespeicherte Läufe ausgewählt werden. Der Zuschlag
kann nachträglich in 5- oder 10-Sekunden-Schritten korrigiert werden; die
ursprünglich gemessene Ist-Zeit bleibt dabei unverändert. Fehlerhafte Läufe
können nach einer zusätzlichen Sicherheitsabfrage endgültig gelöscht werden.
Jede Änderung an einem bereits gespeicherten Lauf muss ausdrücklich bestätigt
werden.

## Startsignal über AUX oder Bluetooth

Unter **Einstellungen → Startsignal** stehen drei Ausgänge zur Auswahl:

- **Aus** startet den Timer wie bisher sofort.
- **AUX** verwendet den analogen beziehungsweise aktuell eingerichteten
  Systemausgang. Ein AUX-Kabel muss nur eingesteckt werden; es gibt kein Pairing.
- **Bluetooth** sucht nach Lautsprechern in der Nähe und kann das gewählte Gerät
  koppeln, als vertrauenswürdig speichern und verbinden.

Die feste **Startverzögerung** wird in Millisekunden eingestellt. Ihr
Wertebereich reicht von 0 ms bis zur automatisch erkannten Länge des Clips, beim
mitgelieferten Signal also bis 17.512 ms. Beim ersten Tasterdruck beginnt das
Startsignal. Nach der eingestellten Zeit startet die Messung automatisch,
während der Ton weiterläuft. Der Ton endet entweder mit dem Clip oder sofort,
wenn die laufende Stoppuhr gestoppt wird. Während der Verzögerung werden weitere
Tasterdrücke ignoriert.

Mit **Testton** lässt sich der Ausgang vor dem Lauf prüfen. Ein gespeicherter
Bluetooth-Lautsprecher wird bei Bedarf vor dem Startsignal erneut verbunden.
Raspberry Pi OS Lite enthält standardmäßig nur ALSA. Das Installationsskript
ergänzt PipeWire, WirePlumber sowie die benötigten Bluetooth-, ALSA- und
PulseAudio-Kompatibilitätswerkzeuge und startet die Audio-Benutzersitzung beim
Booten ohne lokale Anmeldung.

Die unveränderte Quelldatei liegt unter
`src/takt/assets/start_signal_source.mp3`. Für eine zuverlässige Wiedergabe ohne
zusätzlichen MP3-Decoder liefert TAKT außerdem
`src/takt/assets/start_signal.wav` mit demselben Audioinhalt aus.

## Raspberry Pi sicher herunterfahren

In **Einstellungen → System** steht auf Raspberry-Pi-Hardware die Aktion
**Raspberry Pi herunterfahren** zur Verfügung. Sie verlangt eine Bestätigung,
weist auf einen eventuell ungespeicherten Lauf hin und fordert anschließend ein
geordnetes Herunterfahren über `systemctl poweroff` an. Auf Entwicklungsrechnern
ist die Schaltfläche deaktiviert.

Das Installationsskript vergibt dafür ausschließlich die passwortlose
Berechtigung für `systemctl poweroff`; andere Administratorbefehle werden nicht
freigeschaltet. Wenn Raspberry Pi OS die Anfrage ablehnt, bleibt TAKT geöffnet
und zeigt einen Fehler.

## Raspberry-Pi-Taster

Der normalerweise offene Taster wird so angeschlossen:

```text
GPIO17 (BCM) ↔ COM
GND          ↔ NO
```

Die Eingabe ist active-low und verwendet den internen Pull-up-Widerstand.
Pin, Entprellzeit und Doppeldruckfenster stehen in `config.example.toml`.

## Raspberry-Pi-Betrieb prüfen

Der Serverstatus kann bei der Fehlersuche angezeigt werden:

```bash
systemctl status takt.service
```

Die letzten Meldungen stehen im Systemprotokoll:

```bash
journalctl -u takt.service -n 100 --no-pager
```

TAKT verwendet produktiv ausdrücklich `GPIOZERO_PIN_FACTORY=lgpio`. Der
Systemdienst startet nach einem Fehler automatisch neu. Der frühere manuelle
PySide-Autostart wird vom Installationsskript entfernt, damit nur ein Prozess
GPIO und Datenbank kontrolliert.

Der GPIO-Taster reagiert auf die erste fallende Flanke sofort. Der Wert
`bounce_seconds` unterdrückt anschließend nur weitere Flanken durch
Kontaktprellen; er ist keine Mindestdauer für einen Tastendruck.

Die Bluetooth-Suche verwendet ein kurzes Suchfenster und liest gefundene
Geräte parallel aus. Ein bereits gekoppelter Lautsprecher wird beim Verbinden
nicht erneut gekoppelt. Dadurch bleiben bestehende Kopplungen erhalten und die
Verbindung ist im Normalfall deutlich schneller.

## Konfiguration und Daten

Eine eigene Konfiguration kann unter `~/.config/takt/config.toml` abgelegt
werden. Ohne Datei gelten sichere Standardwerte. Die Laufdaten liegen unter
`~/.local/share/takt/takt.db`, tägliche Sicherungen unter
`~/.local/share/takt/backups/` und rotierende Protokolle unter
`~/.local/state/takt/logs/`.

Der Webserver kann ebenfalls konfiguriert werden:

```toml
[server]
host = "0.0.0.0"
port = 8080
```

Der installierte Systemdienst überschreibt den Port mit 80, damit
`http://takt.local` ohne Portangabe funktioniert.

Für einen isolierten Test lässt sich ein anderer Speicherort angeben:

```bash
.venv/bin/takt --windowed --mock-gpio --mock-buzzer \
  --database /tmp/takt-test.db
```

## Tests

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
```

Mit installierten Entwicklungsabhängigkeiten:

```bash
.venv/bin/ruff check .
.venv/bin/pytest
```

## Noch offen

- Installationsskript und Autostart auf dem konkreten Raspberry Pi wiederholt
  validieren,
- GPIO-Taster in wiederholten Hardware-Zyklen validieren,
- visuelle Screenshot-Tests bei 1280×720 und 1920×1080 ergänzen,
- optionalen Nur-Anzeige-Zugriff für zusätzliche Netzwerkgeräte ergänzen.
