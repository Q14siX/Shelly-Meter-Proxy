# Shelly Meter Proxy – Release / Erstveröffentlichung

<p align="center">
  <img src="custom_components/shelly_meter_proxy/brand/logo.png" alt="Shelly Meter Proxy Logo" width="720">
</p>

## Version
**20261023.233307**

---

## Deutsch

### Erstveröffentlichung
Dies ist die **Erstveröffentlichung** von **Shelly Meter Proxy**.

### Enthalten in dieser Version
- Virtuelle **Shelly Pro 3EM**-Instanz für Home Assistant
- Import der Identität des Original-Shelly Pro 3EM
- Import der Shelly-Cloud-Daten des Originalgeräts
- Einrichtungsassistent zum geführten Import und zur sicheren Übergabe
- Auswahl eines Gesamtleistungssensors oder von drei Phasensensoren
- Verteilung eines Gesamtleistungssensors auf L1/L2/L3 oder nur auf L1
- Optionales Umkehren des Vorzeichens
- Prüfung veralteter Messwerte
- Cloud-/TLS-Einstellungen über die Integrationsoptionen
- Zertifikatsprüfung, Fingerprint-Modus und Kompatibilitätsmodus
- Geführte automatische Isolierung des Original-Shelly über geschützten Access Point
- Wartungsmodus für spätere Arbeiten am Originalgerät
- Diagnose-Sensoren für Cloud, Bridge, Quellenstatus und Originalgerät
- Schalter zum Aktivieren/Deaktivieren der Shelly-Cloud-Verbindung
- Zweisprachige Oberfläche in **Deutsch und Englisch**
- Integration von **ICON** und **LOGO** mit transparentem Hintergrund innerhalb des Integrationsordners

### Dokumentation und Branding
- README und RELEASE sind zweisprachig ausgeführt.
- Das Branding liegt ausschließlich unter:
  - `custom_components/shelly_meter_proxy/brand/icon.png`
  - `custom_components/shelly_meter_proxy/brand/logo.png`
- Es existiert kein Root-Ordner `brand`.
- Die Bildreferenzen in `README.md` und `RELEASE.md` zeigen hart auf den Integrations-Unterordner `brand`.

### Hinweise zur Nutzung
- Originalgerät und virtuelle Instanz dürfen nicht gleichzeitig online sein.
- Für die Shelly-Cloud-Nutzung muss die Geräteidentität des Originalgeräts erfolgreich übernommen werden.
- Für sichere TLS-Nutzung sollte nach Möglichkeit Zertifikatsprüfung oder Fingerprint-Prüfung verwendet werden.

---

## English

### Initial release
This is the **initial release** of **Shelly Meter Proxy**.

### Included in this version
- Virtual **Shelly Pro 3EM** instance for Home Assistant
- Import of the identity of the original Shelly Pro 3EM
- Import of the Shelly Cloud data from the original device
- Setup wizard for guided import and secure handover
- Selection of a total power sensor or three phase sensors
- Distribution of a total power sensor across L1/L2/L3 or assignment to L1 only
- Optional sign inversion
- Stale-data validation
- Cloud/TLS settings through the integration options
- Certificate validation, fingerprint mode and compatibility mode
- Guided automatic isolation of the original Shelly through a protected access point
- Maintenance mode for later work on the original device
- Diagnostic sensors for cloud, bridge, source status and original device
- Switch for enabling/disabling the Shelly Cloud connection
- Bilingual user interface in **German and English**
- Included transparent **ICON** and **LOGO** inside the integration folder

### Documentation and branding
- README and RELEASE are bilingual.
- Brand assets are stored exclusively at:
  - `custom_components/shelly_meter_proxy/brand/icon.png`
  - `custom_components/shelly_meter_proxy/brand/logo.png`
- There is no root-level `brand` folder.
- The image references in `README.md` and `RELEASE.md` hard-link to the integration subfolder `brand`.

### Usage notes
- The original device and the virtual instance must not be online at the same time.
- The device identity of the original Shelly must be imported successfully for Shelly Cloud use.
- For secure TLS operation, certificate validation or fingerprint validation should be preferred whenever possible.
