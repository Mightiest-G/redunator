// Redunator configuration.
//
// GOOGLE MAPS API KEY — injected at build time, never committed.
// ------------------------------------------------------------------
// The key comes from the MAPS_API_KEY environment variable:
//   * CI: the GitHub Actions secret GOOGLE_MAPS_API_KEY, passed to
//     the build as env MAPS_API_KEY (for the Android manifest, via
//     app/build.gradle.kts) and as --dart-define=MAPS_API_KEY=...
//     (for this const). See .github/workflows/build-apk.yml.
//   * Local: `MAPS_API_KEY=<key> flutter build apk` (and pass
//     --dart-define=MAPS_API_KEY=<key> so the Dart side sees it).
// A key used to live here as a const and was committed to the public
// repo — GitGuardian flagged it exposed twice, and it was rotated
// out (apk-19). NEVER paste a real key into this repo again: a
// committed key is exposed the moment the repo is public.
// The key needs "Maps SDK for Android" and "Directions API"
// (Directions is only used as a fallback if OSRM routing fails —
// see lib/api/routing.dart). No app restriction: CI signs with the
// release key and local builds with a rotating debug key, so no
// SHA-1 restriction can hold.
//
// When the key is empty, the app does NOT crash and does NOT show a
// blank map: the Map tab shows a full-screen explanation with these
// steps instead (see lib/widgets/missing_key_view.dart).
const String googleMapsApiKey = String.fromEnvironment(
  'MAPS_API_KEY',
  defaultValue: '',
);

/// Default backend base URL: the live Cloud Run service
/// (https://redunator-api-108691448805.us-central1.run.app).
/// Overridable in Settings; a saved Settings value wins over this.
/// For local development use the emulator address
/// http://10.0.2.2:8000, or a LAN IP for a physical device.
const String defaultBaseUrl =
    'https://redunator-api-108691448805.us-central1.run.app';

/// Whether the key above is present. Used by the UI to decide between
/// the real map and the missing-key explanation.
bool get hasMapsApiKey => googleMapsApiKey.isNotEmpty;
