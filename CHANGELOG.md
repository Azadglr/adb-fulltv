# Changelog

## v0.1.0

- Fixed secondary entity names so they no longer all fall back to `ADB FullTV`.
- Added stable English and Turkish names for Remote/Kontrol, Screen/Ekran and On-screen notification/Bildirim.
- Added a single test workflow covering localization, version format and private-file exclusions.
- Added native Home Assistant brand images for Home Assistant 2026.3+.
- Added complete English and Turkish translations for selectors, services and entity names.
- Localized app source labels according to the configured Home Assistant language.
- Added the Home Assistant Hassfest validation workflow required for HACS Marketplace review.
- Removed a device-specific Cast dependency; media metadata now comes from the configured TV only.

## Pre-release development

- First public release.
- Unified Android TV Remote v2, ADB media metadata, screenshots and on-screen notifications.
- Added application sources, app-friendly names, media artwork fallback and YouTube channel metadata.
- Made long-running connection work safe for Home Assistant startup.
- Added reliable ADB fallback for powering off compatible TCL / Google TV devices.
