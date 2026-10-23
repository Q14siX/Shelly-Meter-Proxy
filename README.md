# Shelly Meter Proxy

<p align="center">
  <img src="custom_components/shelly_meter_proxy/brand/logo.png" alt="Shelly Meter Proxy Logo" width="720">
</p>

<p align="center">
  <img src="custom_components/shelly_meter_proxy/brand/icon.png" alt="Shelly Meter Proxy Icon" width="160">
</p>

---

## Deutsch

### Zweck
**Shelly Meter Proxy** ist eine benutzerdefinierte Home-Assistant-Integration, die einen virtuellen **Shelly Pro 3EM** bereitstellt.
Die virtuelle Instanz übernimmt die Geräteidentität und die Shelly-Cloud-Anbindung des physischen Originalgeräts und speist Messwerte aus frei wählbaren Home-Assistant-Sensoren ein.

Damit kann ein bestehendes Shelly-Pro-3EM-Gerät in der Shelly-App bzw. in cloudbasierten Gegenstellen weitergenutzt werden, obwohl die eigentlichen Leistungswerte aus Home Assistant stammen.

### Typische Einsatzfälle
- Übernahme von Leistungswerten aus **Tibber Pulse**
- Übernahme von Leistungswerten anderer Energiezähler oder Integrationen in Home Assistant
- Weiterverwendung eines vorhandenen Shelly Pro 3EM in einer App oder Plattform, die explizit einen Shelly erwartet

### Hauptfunktionen
- Virtuelle Shelly-Pro-3EM-Instanz mit lokaler HTTP-/RPC-Erreichbarkeit
- Import der Geräteidentität des Original-Shelly
- Import und Weiterverwendung der Shelly-Cloud-Daten des Originalgeräts
- Auswahl von Messwertquellen aus Home Assistant:
  - ein einzelner Gesamtleistungssensor oder
  - drei Phasensensoren
- Zuordnung eines Gesamtleistungssensors:
  - gleichmäßig auf L1/L2/L3 verteilen oder
  - nur L1 zuweisen
- Optionales Umkehren des Vorzeichens
- Überwachung veralteter Messwerte
- Automatischer Wartungs-/Isolationsablauf für den Original-Shelly
- Cloud-/TLS-Konfiguration direkt aus der Integration
- Zusätzliche Diagnose-Sensoren und ein Cloud-Schalter
- Zweisprachige Oberfläche: **Deutsch und Englisch**

### Wichtiger Hinweis
Der **Original-Shelly** und der **virtuelle Shelly** dürfen **nicht gleichzeitig** mit derselben Geräteidentität online sein.

Darum führt der Assistent bewusst durch eine Übergabe:
1. Original-Shelly lokal auslesen
2. Messwertquellen auswählen
3. Original-Shelly vollständig trennen oder automatisch isolieren
4. Virtuelle Instanz freigeben

### Enthaltene Entitäten
Je nach Konfiguration stellt die Integration unter anderem folgende Entitäten bereit:
- Netzleistung / Grid power
- Leistung L1 / Power L1
- Leistung L2 / Power L2
- Leistung L3 / Power L3
- Messwerte gültig / Measurements valid
- Cloud-WebSocket / Cloud WebSocket
- Virtuelle Instanz aktiv / Virtual instance active
- Original-Shelly erreichbar / Original Shelly reachable
- Quellenstatus / Source status
- Original-Shelly Geräteinformationen / Original Shelly device information
- Cloud-Serverstatus / Cloud server status
- Schalter: **Shelly Cloud**

### Installation
#### Manuell
1. ZIP entpacken.
2. Den Ordner `custom_components/shelly_meter_proxy/` nach Home Assistant unter `config/custom_components/shelly_meter_proxy/` kopieren.
3. Home Assistant neu starten.
4. Unter **Einstellungen → Geräte & Dienste → Integration hinzufügen** nach **Shelly Meter Proxy** suchen.

#### Per Update-/Paketfunktion
Dieses ZIP ist als einzelnes Paket aufgebaut und kann sowohl für eine Neuinstallation als auch als Update-Paket verwendet werden.

### Ersteinrichtung
1. **Original-Shelly-IP** eingeben.
2. Falls nötig **Shelly-Gerätepasswort** angeben.
3. Messwertquelle auswählen.
4. Gewünschte Home-Assistant-Sensoren wählen.
5. Falls erforderlich Cloud-Zertifikat bzw. Fingerprint bestätigen.
6. Original-Shelly abschalten oder den geführten Isolationsablauf verwenden.
7. Danach arbeitet die virtuelle Instanz mit der importierten Geräteidentität.

### Sprachunterstützung
- `strings.json` enthält die Standardsprache der Integration.
- `translations/de.json` enthält die deutsche Übersetzung.
- `translations/en.json` enthält die englische Übersetzung.

Zusätzlich wurden die wichtigsten Entitätsnamen auf Übersetzungsschlüssel umgestellt.

### Branding
Das Branding liegt **ausschließlich innerhalb des Integrationsordners**:
- `custom_components/shelly_meter_proxy/brand/icon.png`
- `custom_components/shelly_meter_proxy/brand/logo.png`

Es gibt **keinen** separaten `brand`-Ordner im Root-Pfad.
Die Bildpfade in dieser `README.md` und in der `RELEASE.md` verweisen hart auf diesen Integrations-Unterordner.

### Hinweise
- Die Integration ist für **Shelly Pro 3EM Gen2** ausgelegt.
- Für die Shelly-Cloud-Übernahme muss das Originalgerät passende Cloud-Informationen liefern.
- Je nach TLS-Modus kann eine Zertifikats- oder Fingerprint-Bestätigung erforderlich sein.
- Wird der Original-Shelly später für Wartung erneut eingeschaltet, sollte zuvor der Wartungsmodus der Integration verwendet werden.

---

## English

### Purpose
**Shelly Meter Proxy** is a custom Home Assistant integration that provides a virtual **Shelly Pro 3EM**.
The virtual instance takes over the device identity and Shelly Cloud connectivity of the physical original device and feeds it with measurement values from freely selectable Home Assistant sensors.

This allows an existing Shelly Pro 3EM device to keep working in the Shelly app or in cloud-based counterpart systems even though the actual power values come from Home Assistant.

### Typical use cases
- Use power values from **Tibber Pulse**
- Use power values from other energy meters or Home Assistant integrations
- Keep using an existing Shelly Pro 3EM in an app or platform that explicitly expects a Shelly device

### Main features
- Virtual Shelly Pro 3EM instance with local HTTP/RPC reachability
- Import of the original Shelly device identity
- Import and reuse of the Shelly Cloud data from the original device
- Selection of measurement sources from Home Assistant:
  - one total power sensor, or
  - three phase sensors
- Mapping modes for a total power sensor:
  - distribute evenly across L1/L2/L3, or
  - assign to L1 only
- Optional sign inversion
- Stale-data monitoring
- Automatic maintenance/isolation workflow for the original Shelly
- Cloud/TLS configuration directly from the integration
- Additional diagnostic sensors and a cloud switch
- Bilingual user interface: **German and English**

### Important notice
The **original Shelly** and the **virtual Shelly** must **never be online at the same time** with the same device identity.

For that reason, the setup wizard intentionally guides through a handover process:
1. Read the original Shelly locally
2. Select measurement sources
3. Fully disconnect or automatically isolate the original Shelly
4. Enable the virtual instance

### Included entities
Depending on the configuration, the integration provides entities such as:
- Grid power / Netzleistung
- Power L1 / Leistung L1
- Power L2 / Leistung L2
- Power L3 / Leistung L3
- Measurements valid / Messwerte gültig
- Cloud WebSocket / Cloud-WebSocket
- Virtual instance active / Virtuelle Instanz aktiv
- Original Shelly reachable / Original-Shelly erreichbar
- Source status / Quellenstatus
- Original Shelly device information / Original-Shelly Geräteinformationen
- Cloud server status / Cloud-Serverstatus
- Switch: **Shelly Cloud**

### Installation
#### Manual
1. Extract the ZIP.
2. Copy the folder `custom_components/shelly_meter_proxy/` to `config/custom_components/shelly_meter_proxy/` in Home Assistant.
3. Restart Home Assistant.
4. Go to **Settings → Devices & Services → Add Integration** and search for **Shelly Meter Proxy**.

#### Via update/package function
This ZIP is structured as a single package and can be used both as a fresh installation package and as an update package.

### Initial setup
1. Enter the **original Shelly IP**.
2. If required, enter the **Shelly device password**.
3. Select the measurement source mode.
4. Select the desired Home Assistant sensors.
5. If required, confirm the cloud certificate or fingerprint.
6. Power off the original Shelly or use the guided isolation workflow.
7. After that, the virtual instance runs with the imported device identity.

### Language support
- `strings.json` contains the default language of the integration.
- `translations/de.json` contains the German translation.
- `translations/en.json` contains the English translation.

In addition, the most important entity names were moved to translation keys.

### Branding
Brand assets are stored **only inside the integration folder**:
- `custom_components/shelly_meter_proxy/brand/icon.png`
- `custom_components/shelly_meter_proxy/brand/logo.png`

There is **no** separate `brand` directory in the root path.
The image links in this `README.md` and in `RELEASE.md` hard-link to that integration subfolder.

### Notes
- The integration is designed for **Shelly Pro 3EM Gen2**.
- To take over the Shelly Cloud connection, the original device must provide usable cloud information.
- Depending on the TLS mode, certificate or fingerprint confirmation may be required.
- If the original Shelly has to be powered on again later for maintenance, the integration's maintenance mode should be used first.
